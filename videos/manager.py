import os
import sys
import ssl
import time
import logging
import requests
import traceback
import threading
import subprocess
from django.db import transaction
from django.core.files import File
from urllib.parse import urlparse
from .models import Video

logger = logging.getLogger(__name__)


class LegacySSLAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, verify=True, *args, **kwargs):
        self.verify = verify
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        try:
            ctx.options |= getattr(ssl, 'OP_LEGACY_SERVER_CONNECT', 0x4)
        except AttributeError:
            pass
        
        if not self.verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
        pool_kwargs['ssl_context'] = ctx
        return super().init_poolmanager(connections, maxsize, block, **pool_kwargs)


class VideoDownloadManager:
    def __init__(self):
        self.active_downloads = {}
        self.lock = threading.Lock()
    
    def download_video(self, video_instance):
        try:
            with transaction.atomic():
                video = Video.objects.select_for_update().get(pk=video_instance.pk)
                if video.status in ['completed', 'downloading']:
                    return False
                
                video.status = 'downloading'
                video.save()

            thread = threading.Thread(
                target=self._download_thread,
                args=(video_instance,)
            )
            thread.daemon = True
            thread.start()
            
            with self.lock:
                self.active_downloads[video_instance.id] = {
                    'thread': thread,
                    'started_at': time.time(),
                    'video': video_instance
                }

            logger.info(f"Download thread started for: {video_instance.title}")
            return True
            
        except Exception as e:
            sys.stderr.write(f"[DOWNLOAD MANAGER] Failed to start download: {e}\n")
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            return False

    def get_download_status(self, video_id):
        try:
            video = Video.objects.get(id=video_id)
            if video_id in self.active_downloads:
                thread_info = self.active_downloads[video_id]
                if thread_info['thread'].is_alive():
                    return {
                        'status': 'downloading',
                        'thread_alive': True,
                        'started_at': thread_info['started_at'],
                        'duration': time.time() - thread_info['started_at']
                    }
            
            return {
                'status': video.status,
                'thread_alive': False,
                'file_exists': bool(video.video_file and video.video_file.path)
            }
            
        except Exception as e:
            logger.error(f"Error checking download status: {e}")
            return {'status': 'error', 'error': str(e)}
        
    def _convert_to_mp4(self, input_path: str):
        try:
            output_path = os.path.splitext(input_path)[0] + ".mp4"
            logger.info(f"Converting {input_path} to {output_path}...")
            
            try:
                copy_command = [
                    'ffmpeg', '-y',
                    '-loglevel', 'error',
                    '-i', input_path,
                    '-c', 'copy',
                    '-movflags', '+faststart',
                    output_path
                ]
                subprocess.run(
                    copy_command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=True
                )
                if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                    return output_path
            except subprocess.CalledProcessError:
                if os.path.exists(output_path):
                    os.remove(output_path)

            fallback_command = [
                'ffmpeg', '-y',
                '-threads', '1',
                '-loglevel', 'error',
                '-i', input_path,
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-x264-params', 'rc-lookahead=5:bframes=1:threads=1',
                '-c:a', 'aac',
                '-movflags', '+faststart',
                output_path
            ]
            subprocess.run(
                fallback_command, 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.PIPE,
                check=True
            )
            
            if os.path.exists(output_path):
                return output_path
            return None
            
        except Exception as e:
            sys.stderr.write(f"[CONVERSION ERROR] FFmpeg process failed: {e}\n")
            sys.stderr.flush()
            return None
    
    def _generate_thumbnail(self, video_path: str) -> str:
        try:
            thumb_path = os.path.splitext(video_path)[0] + "_thumb.jpg"
            command = [
                'ffmpeg', '-y',
                '-ss', '00:05:00',
                '-i', video_path,
                '-vframes', '1',
                '-q:v', '2',
                thumb_path
            ]
            subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True
            )
            if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 100:
                return thumb_path
        except subprocess.CalledProcessError:
            try:
                command = [
                    'ffmpeg', '-y',
                    '-ss', '00:00:00',
                    '-i', video_path,
                    '-vframes', '1',
                    '-q:v', '2',
                    thumb_path
                ]
                subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=True
                )
                if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 100:
                    return thumb_path
            except Exception as e:
                sys.stderr.write(f"[THUMBNAIL ERROR] Fallback failed: {e}\n")
        except Exception as e:
            sys.stderr.write(f"[THUMBNAIL ERROR] Thumbnail extraction failed: {e}\n")
        return None

    def _get_video_duration(self, video_path: str) -> int:
        try:
            command = [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ]
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            duration_str = result.stdout.strip()
            if duration_str:
                return int(float(duration_str))
        except Exception as e:
            sys.stderr.write(f"[DURATION ERROR] Failed to fetch duration: {e}\n")
        return 0
    
    def _download_thread(self, video_instance):
        max_retries = 10
        mode = 'wb'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
        }
        
        try:
            sys.stderr.write(f"\n[DOWNLOAD THREAD] Starting download for: {video_instance.title}\n")
            sys.stderr.write(f"[DOWNLOAD THREAD] Target URL: {video_instance.download_url}\n")
            sys.stderr.flush()
            
            with transaction.atomic():
                video = Video.objects.select_for_update().get(pk=video_instance.pk)
                if video.status == 'completed':
                    return
                video.status = 'downloading'
                video.save()
            
            parsed_url = urlparse(video.download_url)
            filename = os.path.basename(parsed_url.path) or f"video_{video.id}.mp4"
            temp_path = f"/tmp/{filename}"
            
            if os.path.exists(temp_path):
                downloaded_size = os.path.getsize(temp_path)
                if downloaded_size > 0:
                    headers['Range'] = f'bytes={downloaded_size}-'
                    mode = 'ab'
                    sys.stderr.write(f"[DOWNLOAD THREAD] Found partial file. Resuming from {downloaded_size} bytes.\n")
                    sys.stderr.flush()
            
            retries = 0
            verify_ssl = True
            download_success = False
            
            while retries < max_retries:
                try:
                    session = requests.Session()
                    adapter = LegacySSLAdapter(verify=verify_ssl)
                    session.mount('https://', adapter)
                    sys.stderr.write(f"[DOWNLOAD ATTEMPT {retries + 1}/{max_retries}] Starting request (verify_ssl={verify_ssl})...\n")
                    sys.stderr.flush()
                    
                    with session.get(video.download_url, headers=headers, stream=True, verify=verify_ssl, timeout=(15, 60)) as response:
                        sys.stderr.write(f"[DOWNLOAD ATTEMPT {retries + 1}] Received response status: {response.status_code}\n")
                        sys.stderr.flush()
                        
                        if response.status_code == 416:
                            sys.stderr.write("[DOWNLOAD ATTEMPT] Server returned 416 (Range Not Satisfiable). File might already be complete.\n")
                            sys.stderr.flush()
                            download_success = True
                            break
                        
                        if headers.get('Range') and response.status_code == 200:
                            sys.stderr.write("[DOWNLOAD ATTEMPT] Server ignored Range header. Restarting download from beginning.\n")
                            sys.stderr.flush()
                            mode = 'wb'
                            headers.pop('Range', None)
                        
                        if response.status_code not in [200, 206]:
                            sys.stderr.write(f"[SERVER ERROR] Bad response headers: {dict(response.headers)}\n")
                            try:
                                sys.stderr.write(f"[SERVER ERROR] Response body snippet: {response.text[:500]}\n")
                            except Exception:
                                pass
                            sys.stderr.flush()
                        
                        response.raise_for_status()
                        
                        try:
                            with open(temp_path, mode) as f:
                                for chunk in response.iter_content(chunk_size=16384):
                                    if chunk:
                                        f.write(chunk)
                        except Exception as write_err:
                            sys.stderr.write(f"[LOCAL DISK ERROR] Failed to write to {temp_path}: {write_err}\n")
                            sys.stderr.flush()
                            raise write_err
                        
                        download_success = True
                        break
                
                except (requests.exceptions.SSLError, ssl.SSLError) as ssl_err:
                    sys.stderr.write(f"[ATTEMPT ERROR] SSL Handshake exception: {ssl_err}\n")
                    sys.stderr.flush()
                    if verify_ssl:
                        verify_ssl = False
                    else:
                        retries += 1
                        time.sleep(2 * retries)
                
                except (requests.exceptions.RequestException, requests.exceptions.Timeout) as net_err:
                    sys.stderr.write(f"[ATTEMPT ERROR] Network or HTTP error occurred: {net_err}\n")
                    sys.stderr.flush()
                    retries += 1
                    time.sleep(2 * retries)
                    if os.path.exists(temp_path):
                        downloaded_size = os.path.getsize(temp_path)
                        headers['Range'] = f'bytes={downloaded_size}-'
                        mode = 'ab'
            if download_success and os.path.exists(temp_path):
                final_path = temp_path
                file_ext = os.path.splitext(temp_path)[1].lower()
                
                if file_ext not in ['.mp4', '.webm', '.avi']:
                    converted_path = self._convert_to_mp4(temp_path)
                    if converted_path and os.path.exists(converted_path):
                        final_path = converted_path
                        filename = os.path.splitext(filename)[0] + ".mp4"
                        if os.path.exists(temp_path) and temp_path != converted_path:
                            os.remove(temp_path)
                
                if os.path.exists(final_path):
                    thumb_path = self._generate_thumbnail(final_path)
                    duration_seconds = self._get_video_duration(final_path)

                    with open(final_path, 'rb') as f:
                        video.video_file.save(filename, File(f))
                    
                    if thumb_path and os.path.exists(thumb_path):
                        thumb_name = os.path.splitext(filename)[0] + "_thumb.jpg"
                        with open(thumb_path, 'rb') as tf:
                            video.thumbnail.save(thumb_name, File(tf))
                        os.remove(thumb_path)

                    video.duration = duration_seconds
                    video.file_size = os.path.getsize(final_path)
                    video.status = 'completed'
                    video.save()
                    
                    if os.path.exists(final_path):
                        os.remove(final_path)
                    
                    sys.stderr.write(f"[DOWNLOAD SUCCESS] Video database record updated for: {video.title}\n")
                    sys.stderr.flush()
                else:
                    raise Exception("Transcoded file could not be found.")
            else:
                raise Exception("Download failed - Retries exceeded or temp file does not exist.")
                
        except Exception as e:
            sys.stderr.write(f"[DOWNLOAD CRITICAL FAILURE] Thread exception for {video_instance.title}: {e}\n")
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            
            try:
                with transaction.atomic():
                    video = Video.objects.select_for_update().get(pk=video_instance.pk)
                    video.status = 'error'
                    video.error_message = str(e)
                    video.save()
            except Exception as db_error:
                sys.stderr.write(f"[DATABASE ERROR] Could not update fail status: {db_error}\n")
                sys.stderr.flush()
        
        finally:
            with self.lock:
                if video_instance.id in self.active_downloads:
                    del self.active_downloads[video_instance.id]


video_manager = VideoDownloadManager()