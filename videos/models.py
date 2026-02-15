import os
import uuid
import logging
from django.db import models
from django.conf import settings
from django.core.files.storage import FileSystemStorage

logger = logging.getLogger(__name__)

class VideoStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(location=settings.STORAGE_SERVER_PATH)
        

class Video(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('completed', 'Completed'),
        ('error', 'Error'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    download_url = models.URLField(max_length=1000)
    video_file = models.FileField(
        upload_to='videos/',
        storage=VideoStorage(),
        null=True,
        blank=True
    )
    thumbnail = models.ImageField(
        upload_to='thumbnails/',
        storage=VideoStorage(),
        null=True,
        blank=True
    )
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    file_size = models.BigIntegerField(default=0)  # in bytes
    duration = models.IntegerField(default=0)  # in seconds
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
        ]
    
    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"
    
    def get_absolute_path(self):
        if self.video_file:
            return os.path.join(settings.STORAGE_SERVER_PATH, self.video_file.name)
        return None
    
    def get_video_url(self):
        if self.video_file:
            return f"/media/{self.video_file.name}"
        return None
    
    def delete_video_file(self):
        if self.video_file:
            try:
                file_path = self.get_absolute_path()
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"Deleted video file: {file_path}")
                    return True
            except Exception as e:
                logger.error(f"Error deleting video file: {e}")
        return False
    
    def delete_thumbnail_file(self):
        if self.thumbnail:
            try:
                thumbnail_path = os.path.join(settings.STORAGE_SERVER_PATH, self.thumbnail.name)
                if os.path.exists(thumbnail_path):
                    os.remove(thumbnail_path)
                    logger.info(f"Deleted thumbnail: {thumbnail_path}")
                    return True
            except Exception as e:
                logger.error(f"Error deleting thumbnail: {e}")
        return False
    
    def save(self, *args, **kwargs):
        if not self.title and self.download_url:
            self.title = os.path.basename(self.download_url)
        
        super().save(*args, **kwargs)
    
    def delete(self, using=None, keep_parents=False):
        self.delete_video_file()
        self.delete_thumbnail_file()
        super().delete(using=using, keep_parents=keep_parents)
    
    @property
    def file_size_human(self):
        if self.file_size == 0:
            return "0 B"
        
        size = float(self.file_size)
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"
    
    @property
    def duration_human(self):
        if self.duration == 0:
            return "0s"
        
        hours = self.duration // 3600
        minutes = (self.duration % 3600) // 60
        seconds = self.duration % 60
        
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"