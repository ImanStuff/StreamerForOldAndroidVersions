import os
import asyncio
import aiofiles
import mimetypes
from django.conf import settings
from django.db.models import Sum
from django.contrib import messages
from asgiref.sync import sync_to_async
from django.shortcuts import render, redirect
from django.http import StreamingHttpResponse, HttpResponse, HttpRequest, JsonResponse
from .models import Video
from .manager import video_manager


async def a_path_exists(path: str) -> bool:
    return await sync_to_async(os.path.exists)(path)

async def video_list(request: HttpRequest) -> HttpResponse:
    videos = Video.objects.all().order_by('-created_at')
    total_videos = await videos.acount()
    completed_videos = await videos.filter(status='completed').acount()
    total_size_task = await videos.aaggregate(total=Sum('file_size'))
    videos = [video async for video in videos]
    context = {
        'videos': videos,
        'total_videos': total_videos,
        'completed_videos': completed_videos,
        'total_size': total_size_task['total'] or 0,
        'user': await request.auser()
    }
    return render(request, './list.html', context)

async def video_detail(request: HttpRequest, video_id: int) -> JsonResponse:
    try:
        video = await Video.objects.aget(id=video_id)
    except Video.DoesNotExist:
        return JsonResponse({"Error": "Video not found!"}, status=404)
    return JsonResponse({
        'id': video.id or '', 
        'title': video.title or '',
        'description': video.description or '',
    })
async def stream_video(request: HttpRequest, video_id: int) -> HttpResponse | StreamingHttpResponse:
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

async def file_chunk_generator(file_path: str, start: int, length: int, chunk_size: int=8192):
    async with aiofiles.open(file_path, 'rb') as f:
        await f.seek(start)
        remaining = length
        
        while remaining > 0:
            read_size = min(chunk_size, remaining)
            chunk = await f.read(read_size)
            if not chunk:
                break
            remaining -= len(chunk)
            await asyncio.sleep(0.0001)  
            yield chunk

async def delete_video(request: HttpRequest, video_id: int) -> HttpResponse:
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


async def check_download_status(request: HttpRequest, video_id: int) -> HttpResponse | JsonResponse:
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