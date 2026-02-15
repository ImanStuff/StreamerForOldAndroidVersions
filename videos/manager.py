import os
import time
import shlex
import logging
import requests
import threading
import subprocess
from django.db import transaction
from django.core.files import File
from urllib.parse import urlparse
from .models import Video

logger = logging.getLogger(__name__)

class VideoDownloadManager:
    def __init__(self):
        self.active_downloads = {}
        self.lock = threading.Lock()
    
    def download_video(self, video_instance):
        try:
            with transaction.atomic():
                video = Video.objects.select_for_update().get(pk=video_instance.pk)
                if video.status == 'completed':
                    return False  # Already downloaded
                if video.status == 'downloading':
                    return False  # Already downloading
                
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
            logger.error(f"Failed to start download: {e}")
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
            
            command = [
                'ffmpeg', '-y',
                '-i', input_path,
                '-c:v', 'libx264',
                '-c:a', 'aac',
                '-movflags', '+faststart',
                output_path
            ]
            
            process = subprocess.run(
                command, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                check=True
            )
            
            if os.path.exists(output_path):
                return output_path
            return None
            
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg conversion failed: {e.stderr.decode()}")
            return None
        except Exception as e:
            logger.error(f"Conversion error: {e}")
            return None
    
    def _download_thread(self, video_instance):
        max_retries = 10
        mode = 'wb'
        headers = {}
        
        try:
            logger.info(f"=== DOWNLOAD STARTED: {video_instance.title} ===")
            logger.info(f"URL: {video_instance.download_url}")
            
            with transaction.atomic():
                video = Video.objects.select_for_update().get(pk=video_instance.pk)
                if video.status == 'completed':
                    return
                video.status = 'downloading'
                video.save()
            
            logger.info(f"Starting download: {video.title}")
            
            parsed_url = urlparse(video.download_url)
            filename = os.path.basename(parsed_url.path)
            if not filename:
                filename = f"video_{video.id}.mp4"
            
            temp_path = f"/tmp/{filename}"
            
            if os.path.exists(temp_path):
                downloaded_size = os.path.getsize(temp_path)
                if downloaded_size > 0:
                    headers['Range'] = f'bytes={downloaded_size}-'
                    mode = 'ab'
                    logger.info(f"Resuming download from {downloaded_size} bytes")
            
            retries = 0
            while retries < max_retries:
                try:
                    with requests.get(video.download_url, headers=headers, stream=True, timeout=(99999, 99999)) as response:
                        if response.status_code == 416:
                            logger.info("Download already complete (server reported 416).")
                            break
                        if headers.get('Range') and response.status_code == 200:
                            logger.warning("Server doesn't support resume, restarting download")
                            mode = 'wb'
                            headers = {}
                        
                        response.raise_for_status()
                        
                        with open(temp_path, mode) as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                        break
                
                except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                    retries += 1
                    logger.warning(f"Network error: {e}. Retrying ({retries}/{max_retries})...")
                    time.sleep(2 * retries)
                    if os.path.exists(temp_path):
                        downloaded_size = os.path.getsize(temp_path)
                        headers['Range'] = f'bytes={downloaded_size}-'
                        mode = 'ab'
            
            if os.path.exists(temp_path):
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
                    with open(final_path, 'rb') as f:
                        video.video_file.save(filename, File(f))
                    
                    video.file_size = os.path.getsize(final_path)
                    video.status = 'completed'
                    video.save()
                    if os.path.exists(final_path):
                        os.remove(final_path)
                    
                    logger.info(f"Download completed: {video.title} ({video.file_size_human})")
                else:
                    raise Exception("Final file not found after conversion")
            else:
                raise Exception("Download failed - temp file not found")

                
        except Exception as e:
            logger.error(f"Download failed for {video_instance.title}: {e}")
            try:
                with transaction.atomic():
                    video = Video.objects.select_for_update().get(pk=video_instance.pk)
                    video.status = 'error'
                    video.error_message = str(e)
                    video.save()
            except Exception as db_error:
                logger.error(f"Failed to update video status: {db_error}")
        
        finally:
            with self.lock:
                if video_instance.id in self.active_downloads:
                    del self.active_downloads[video_instance.id]

video_manager = VideoDownloadManager()