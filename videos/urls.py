from django.urls import path
from django.views.static import serve
from django.conf import settings
from . import views

urlpatterns = [
    path('', views.video_list, name='video_list'),
    path('video/<uuid:video_id>/', views.video_detail, name='video_detail'),
    path('video/<uuid:video_id>/stream/', views.stream_video, name='stream_video'),
    path('video/<uuid:video_id>/delete/', 
        views.delete_video, 
        name='delete_video'),
    path('video/<uuid:video_id>/status/', 
        views.check_download_status, 
        name='check_download_status'),
    path('movie-proxy/', views.movie_proxy, name='movie_proxy'),
    path('test-movie/', views.tv_remote_view, name='tv_remote_view_'),
    path('video/<uuid:video_id>/play/', views.play_video, name='play_video'),
    path('video/<uuid:video_id>/log-time/', views.log_video_time, name='log_video_time'),
]

if settings.DEBUG:
    urlpatterns += [
        path('media/<path:path>', serve, {
            'document_root': settings.MEDIA_ROOT,
            'show_indexes': settings.DEBUG,
        }),
    ]