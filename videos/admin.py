from django.contrib import admin
from django.utils.html import format_html
from django.urls import path, reverse
from django.db import transaction
from django.shortcuts import redirect
from django.contrib import messages
from .models import Video
from .manager import video_manager

@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = ('title', 'status_badge', 'file_size_display', 'created_at', 'video_actions')
    list_filter = ('status', 'created_at')
    search_fields = ('title', 'download_url')
    readonly_fields = ('status', 'file_size', 'created_at', 'updated_at', 'video_preview')
    fieldsets = (
        ('Video Information', {
            'fields': ('title', 'description', 'download_url')
        }),
        ('Video File', {
            'fields': ('video_file', 'thumbnail', 'video_preview')
        }),
        ('Metadata', {
            'fields': ('status', 'file_size', 'duration', 'error_message')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['download_selected_videos', 'delete_files_selected']
    
    def status_badge(self, obj):
        colors = {
            'pending': 'gray',
            'downloading': 'blue',
            'completed': 'green',
            'error': 'red'
        }
        thread_info = ""
        if obj.status == 'downloading':
            status_info = video_manager.get_download_status(obj.id)
            if status_info.get('thread_alive'):
                thread_info = ' (active)'
            else:
                thread_info = ' (stalled)'
                return format_html(
                    '<span style="padding: 2px 8px; border-radius: 10px; background: orange; color: white;">STALLED</span>'
                )
        
        return format_html(
            '<span style="padding: 2px 8px; border-radius: 10px; background: {}; color: white;">{}{}</span>',
            colors.get(obj.status, 'gray'),
            obj.get_status_display(),
            thread_info
        )
    status_badge.short_description = 'Status'
    
    def file_size_display(self, obj):
        return obj.file_size_human if obj.file_size > 0 else "—"
    file_size_display.short_description = 'Size'

    def video_actions(self, obj):
        buttons = []
        is_stalled = False
        if obj.status == 'downloading':
            status_info = video_manager.get_download_status(obj.id)
            if not status_info.get('thread_alive'):
                is_stalled = True
        if obj.status in ['pending', 'error'] or is_stalled:
            btn_label = "Retry Download" if is_stalled else "Download"
            btn_color = "#FF9800" if is_stalled else "#4CAF50"
            
            buttons.append(
                format_html(
                    '<a class="button" href="{}" style="background: {}; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; margin-right: 5px;">{}</a>',
                    reverse('admin:videos_video_download', args=[obj.id]),
                    btn_color,
                    btn_label
                )
            )
            
        buttons.append(
            format_html(
                '<a class="button" href="{}" style="background: #607D8B; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; margin-right: 5px;" title="Check download status">Status</a>',
                reverse('check_download_status', args=[obj.id])
            )
        )
        if obj.status == 'completed' and obj.video_file:
            buttons.append(
                format_html(
                    '<a class="button" href="{}" target="_blank" style="background: #2196F3; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; margin-right: 5px;">Play</a>',
                    obj.get_video_url()
                )
            )
        if obj.video_file:
            buttons.append(
                format_html(
                    '<a class="button" href="{}" style="background: #f44336; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px;" onclick="return confirm(\'Delete video files?\\nThis will not delete the database record.\')">Delete Files</a>',
                    reverse('admin:videos_video_delete_files', args=[obj.id])
                )
            )
        
        return format_html(' '.join(buttons))
    video_actions.short_description = 'Actions'
    
    def video_preview(self, obj):
        if obj.video_file:
            return format_html(
                '''
                <div style="max-width: 400px;">
                    <video width="100%" controls>
                        <source src="{}" type="video/mp4">
                        Your browser does not support the video tag.
                    </video>
                    <div style="margin-top: 10px;">
                        <strong>Path:</strong> {}<br>
                        <strong>Size:</strong> {}<br>
                    </div>
                </div>
                ''',
                obj.get_video_url(),
                obj.get_absolute_path(),
                obj.file_size_human
            )
        return "No video file"
    video_preview.short_description = 'Video Preview'
    
    def download_selected_videos(self, request, queryset):
        count = 0
        for video in queryset:
            if video.status in ['pending', 'error', 'downloading']:
                video.status = 'pending'
                video.save()
                if video_manager.download_video(video):
                    count += 1
        self.message_user(request, f"Started download for {count} videos")
    download_selected_videos.short_description = "Download selected videos"
    
    def delete_files_selected(self, request, queryset):
        deleted_count = 0
        for video in queryset:
            if video.delete_video_file():
                video.video_file = None
                video.thumbnail = None
                video.status = 'pending'
                video.save()
                deleted_count += 1
        
        self.message_user(request, f"Deleted files for {deleted_count} videos")
    delete_files_selected.short_description = "Delete files from selected videos"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        should_download = False
        
        if obj.download_url:
            if not change:
                should_download = True
            elif obj.status == 'pending':
                should_download = True
        
        if should_download:
            transaction.on_commit(lambda: video_manager.download_video(obj))

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<uuid:video_id>/download/', 
                 self.admin_site.admin_view(self.download_view), 
                 name='videos_video_download'),
            path('<uuid:video_id>/delete-files/', 
                 self.admin_site.admin_view(self.delete_files_view), 
                 name='videos_video_delete_files'),
        ]
        return custom_urls + urls
    
    def download_view(self, request, video_id):
        video = Video.objects.get(id=video_id)
        if video.status == 'downloading':
            video.status = 'pending'
            video.save()
            
        success = video_manager.download_video(video)
        
        if success:
            messages.success(request, f'Started download for "{video.title}"')
        else:
            messages.warning(request, f'Could not start download for "{video.title}" (Check logs)')
            
        return redirect('admin:videos_video_changelist')
    
    def delete_files_view(self, request, video_id):
        video = Video.objects.get(id=video_id)
        video_deleted = video.delete_video_file()
        thumbnail_deleted = video.delete_thumbnail_file()
        
        if video_deleted or thumbnail_deleted:
            video.video_file = None
            video.thumbnail = None
            video.status = 'pending'
            video.file_size = 0
            video.save()
            messages.success(request, f'Deleted files for "{video.title}"')
        else:
            messages.warning(request, f'No files found for "{video.title}"')
        
        return redirect('admin:videos_video_changelist')