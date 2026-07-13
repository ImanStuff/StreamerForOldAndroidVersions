import os
import json
import asyncio
import requests
import aiofiles
import mimetypes
from bs4 import BeautifulSoup
from django.utils.html import escape
from asgiref.sync import sync_to_async
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.http import FileResponse, StreamingHttpResponse, HttpResponse, HttpRequest, JsonResponse
from django.template.defaultfilters import filesizeformat
from django.db.models import Sum, Max
from django.contrib import messages
from django.conf import settings
from .models import Video, Logging
from .manager import video_manager


async def a_path_exists(path):
    return await sync_to_async(os.path.exists)(path)

async def video_list(request: HttpRequest):
    videos = Video.objects.annotate(watched_time_db=Max('logger_video__watched_time')).order_by('-created_at')
    total_videos = await videos.acount()
    completed_videos = await videos.filter(status='completed').acount()
    total_size_task = await videos.aaggregate(total=Sum('file_size'))
    total_size = total_size_task['total'] or 0

    if request.GET.get('format') == 'json':
        video_list_data = []
        async for video in videos:

            thumbnail_url = ""
            if video.thumbnail:
                try:
                    thumbnail_url = video.thumbnail.url
                except ValueError:
                    pass
            
            watched_time = video.watched_time_db or 0
            video_list_data.append({
                'id': str(video.id),
                'title': video.title,
                'status': video.status,
                'status_display': video.get_status_display(),
                'duration_human': video.duration_human,
                'file_size_human': video.file_size_human,
                'thumbnail_url': thumbnail_url,
                'watched_time': watched_time,
            })
        return JsonResponse({
            'videos': video_list_data,
            'total_videos': total_videos,
            'completed_videos': completed_videos,
            'total_size_human': filesizeformat(total_size),
        })

    videos_list = []
    async for video in videos:
        watched = video.watched_time_db or 0
        video.watched_label = f" | Watched: {round(watched / 60)} min" if watched > 0 else ""
        videos_list.append(video)

    last_video = await sync_to_async(lambda: Logging.objects.select_related('video').order_by('-updated').first())()
    context = {
        'videos': videos,
        'total_videos': total_videos,
        'completed_videos': completed_videos,
        'total_size': total_size,
        'user': await request.auser(),
        'last_video': last_video
    }
    return render(request, './list.html', context)

async def video_detail(request: HttpRequest, video_id: int):
    try:
        video = await Video.objects.aget(id=video_id)
    except Video.DoesNotExist:
        return JsonResponse({"Error": "Video not found!"}, status=404)
    return JsonResponse({
        'id': video.id or '', 
        'title': video.title or '',
        'description': video.description or '',
    })
async def stream_video(request: HttpRequest, video_id: int):
    try:
        video = await Video.objects.aget(id=video_id)
    except Video.DoesNotExist: return HttpResponse("Video not found", status=404)
    
    if not video.video_file:
        return HttpResponse("Video file not found", status=404)
    
    file_path = await sync_to_async(video.get_absolute_path)()
    
    if not await a_path_exists(file_path):
        return HttpResponse("Video file not found on storage server")
    
    file_size = os.path.getsize(file_path)
    content_type, encoding = mimetypes.guess_type(file_path)
    content_type = content_type or 'video/mp4'
    range_header = request.headers.get('Range', '').strip()
    async def stream(file_path, start, length):
        async for chunk in file_chunk_generator(file_path, start, length):
            yield chunk
    
    if range_header.startswith('bytes='):
        range_bytes = range_header[6:].split('-')
        start = int(range_bytes[0]) if range_bytes[0] else 0
        end = int(range_bytes[1]) if range_bytes[1] and range_bytes[1] else file_size - 1
        
        if start >= file_size:
            return HttpResponse(status=416)
        
        end = min(end, file_size - 1)
        length = end - start + 1
        response = StreamingHttpResponse(
            stream(file_path, start, length),
            status=206,
            content_type=content_type
        )
        
        response['Content-Range'] = f'bytes {start}-{end}/{file_size}'
        response['Content-Length'] = str(length)
    else:
        response = StreamingHttpResponse(
            stream(file_path, 0, file_size),
            content_type=content_type
        )
        response['Content-Length'] = str(file_size)

    response['Accept-Ranges'] = 'bytes'
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    if 'download' in request.GET:
        response['Content-Disposition'] = f'attachment; filename="{os.path.basename(file_path)}"'
    
    return response

async def file_chunk_generator(file_path, start, length, chunk_size=8192):
    async with aiofiles.open(file_path, 'rb') as f:
        await f.seek(start)
        remaining = length
        
        while remaining > 0:
            read_size = min(chunk_size, remaining)
            chunk = await f.read(read_size)
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk

async def delete_video(request: HttpRequest, video_id: int):
    user = await request.auser()
    if not user.is_authenticated:
        return HttpResponse("No authenticated user found!", status=400)
    if not request.method == 'POST':
        return HttpResponse("Method not allowed!", status=405)
    try:
        video = await Video.objects.aget(id=video_id)
    except Video.DoesNotExist: return HttpResponse("Video not found", status=404)
    video_title = video.title
    await video.adelete()
    
    messages.success(request, f'Video "{video_title}" deleted successfully with all files')
    return redirect('video_list')


async def admin_tools(request: HttpRequest):
    user = await request.auser()
    if not user.is_staff:
        return HttpResponse("Forbiden!", status=403)
    videos = await sync_to_async(Video.objects.all)()
    stats_data = await videos.aaggregate(
        total_size=Sum('file_size')
    )
    stats = {
        'total': await videos.acount(),
        'completed': await videos.filter(status='completed').acount(),
        'downloading': await videos.filter(status='downloading').acount(),
        'pending': await videos.filter(status='pending').acount(),
        'error': await videos.filter(status='error').acount(),
        'total_size': stats_data['total_size'] or 0,
        'storage_path': settings.STORAGE_SERVER_PATH,
    }
    
    return render(request, './admin_tools.html', {'stats': stats})



async def check_download_status(request: HttpRequest, video_id: int) -> JsonResponse:
    user = await request.auser()
    if not user.is_staff:
        return HttpResponse("Forbiden!", status=403)
    try:
        video = await Video.objects.aget(id=video_id)
    except Video.DoesNotExist: return HttpResponse("Video not found")
    status_info = await sync_to_async(video_manager.get_download_status)(video_id)
    file_status = 'not_started'
    if video.video_file:
        try:
            file_path = await sync_to_async(video.get_absolute_path)()
            if await a_path_exists(file_path):
                file_size = os.path.getsize(file_path)
                file_status = f'exists ({file_size} bytes)'
            else:
                file_status = 'missing'
        except:
            file_status = 'error'
    
    return JsonResponse({
        'video_id': str(video.id),
        'title': video.title,
        'database_status': video.status,
        'thread_status': status_info.get('status'),
        'thread_alive': status_info.get('thread_alive', False),
        'file_status': file_status,
        'download_url': video.download_url,
        'created_at': video.created_at.isoformat(),
        'updated_at': video.updated_at.isoformat(),
        'error_message': video.error_message,
    })


@csrf_exempt
async def movie_proxy(request: HttpRequest) -> HttpResponse:
    url = request.GET.get('url')
    if not url or not url.startswith(('http://', 'https://')):
        return HttpResponse("Invalid URL", status=400)
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 4.4.2; Nexus 4 Build/KOT49H) AppleWebKit/537.36'
        }
        resp = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
        
        soup = BeautifulSoup(resp.text, 'html.parser')

        soup = await asyncio.to_thread(android44_safe, soup)
        soup = await asyncio.to_thread(proxy_links, soup, request)
        html = str(soup)
        return HttpResponse(html, content_type='text/html; charset=utf-8')
        
    except Exception as e:
        return HttpResponse(f"Error: {str(e)}", status=500)


def android44_safe(soup: BeautifulSoup):
    for script in soup.find_all('script', src=True):
        if any(x in script['src'] for x in ['es6', 'module', 'webgl', 'webrtc']):
            script.decompose()
    
    css = """
    * { box-sizing: border-box; }
    body { font-family: Arial,sans-serif; margin: 0; padding: 20px; background: #000; color: #fff; }
    .movie-poster { max-width: 100%; height: auto; }
    .movie-grid { display: block; }
    .movie-item { margin: 20px 0; padding: 15px; background: #222; border-radius: 8px; }
    .play-btn { background: #e50914; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-size: 16px; }
    video { width: 100%; max-width: 800px; height: auto; }
    @media (max-width: 600px) { body { padding: 10px; } }
    """
    
    style = soup.new_tag('style')
    style.string = css
    soup.head.insert(0, style)
    
    return soup

def proxy_links(soup: BeautifulSoup, request: HttpRequest):
    proxy_base = request.build_absolute_uri('/movie-proxy?url=')
    
    for a in soup.find_all('a', href=True):
        a['href'] = f"{proxy_base}{a['href']}"
    
    for img in soup.find_all('img', src=True):
        img['src'] = f"{proxy_base}{img['src']}"
    
    for source in soup.find_all('source', src=True):
        source['src'] = f"{proxy_base}{source['src']}"
    
    return soup


async def tv_remote_view(request: HttpRequest) -> HttpResponse:
    return render(request, "./tv-remote.html")


async def play_video(request: HttpRequest, video_id: str):
    try:
        video = await Video.objects.aget(id=video_id)
    except Video.DoesNotExist:
        return HttpResponse("Video not found", status=404)

    log_obj = await Logging.objects.filter(video=video).afirst()
    if request.GET.get('restart') == '1':
        start_time = 0.0
        if log_obj:
            log_obj.watched_time = 0.0
            await log_obj.asave()
    else:
        start_time = log_obj.watched_time if log_obj else 0.0

    return render(request, './player.html', {
        'video': video,
        'start_time': start_time
    })

@csrf_exempt
async def log_video_time(request: HttpRequest, video_id: str):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            current_time = float(data.get('time', 0))
            log = await Logging.objects.filter(video_id=video_id).afirst()
            if not log:
                await Logging.objects.acreate(video_id=video_id, watched_time=current_time)
            else:
                log.watched_time = current_time
                await log.asave(update_fields=['watched_time', 'updated'])
            return JsonResponse({'status': 'ok'})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'bad request'}, status=400)