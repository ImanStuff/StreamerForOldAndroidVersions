# Online streamer on old android versions (4.4)

Recently, i had issues seeing movies in my old android 4.4 TV.
and this code helped me a lot.

Just paste the movie's download url inside the Django admin; The code downloads the movie,
converts it to .mp4 format if needed, and you can see it from site's main page.

# Installation
1- Clone the repo:
```bash
git clone https://github.com/ImanStuff/StreamerForOldAndroidVersions.git
cd StreamerForOldAndroidVersions
```

2- Create a virtualenv and install the requirements:

Linux:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Windows:
```bash
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```


3- Edit Django settings.py based on your OS:
```python
STORAGE_SERVER_PATH = '/mnt/storage_server'   # Linux
# STORAGE_SERVER_PATH = 'D:/storage_server'   # Windows
```

4- Create the required folders:
Linux:
```bash
mkdir -p /mnt/storage_server/{media,databases,logs}
```

Windows:
```bash
New-Item -ItemType Directory -Force -Path "D:\storage_server\media", "D:\storage_server\databases", "D:\storage_server\logs"
```

5- Install ffmpeg:
Linux:
```bash
sudo apt update && sudo apt install ffmpeg
```

Windows:
Vistit https://www.ffmpeg.org/download.html, extract the downloaded file, and
add it to system PATH. check installation from cmd:
```bash
ffmpeg --version
```

6- Run django setups:
```bash
python manage.py migrate
python manage.py createsuperuser
```

```bash
python manage.py collectstatic
```

7- Run the core with daphne:
```bash
daphne -b 0.0.0.0 -p 8000 django_core.asgi:application
```
or you can use supervisor combined with other things like gunicorn + uvicorn.


Visit http://server-ip:8000/ to see added movies and stream them.
Visit http://server-ip:8000/admin and add new videos there. 
just movie name and movie download url are required.

# Network Optimization (Linux users guide)
If you experience buffering, stuttering, or slow video loading when streaming to your devices (especially on Wi-Fi), you should enable TCP BBR. BBR is a modern network congestion algorithm that significantly improves stream stability and throughput.
open sysctl:
```bash
nano /etc/sysctl.conf
```
Add these two lines in the end of it:
```bash
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
```
apply the changes:
```bash
sysctl -p
```
And, verify the BBR activation:
```bash
sysctl net.ipv4.tcp_congestion_control
// Expected output: net.ipv4.tcp_congestion_control = bbr
```


# Notes
- ## 2026/07/17 notes
    - ## Improved UI, and some features.
        1- Some improvements with AI. I will handle it better myself next time.
        2- Making temp_path more specific by adding video_id in it.
        3- Returning True in delete_video_file and delete_thumbnail_file inside models.py,
            to show we deleted the file. ( This is not logically sound. We may have bug that caused the functions could not find files to delete. I will improve it.)
    - ## Problem with old .mp4s : 
        Old version of androids need specific codecs ( H.264 Baseline Profile and yuv420p pixel format, Modern mp4 uses H.256 or H.264 High Profile ). In result, even .mp4 extensions that we were not convert them using ffmpeg, May cause error: "This video can't be played". New version fixes this plus, adding baseline parameter in ffmpeg fallback command.
    - ## Problem with videos' filename: 
        Recent version of code, Does support default Django max_length = 50 for FileField, which raises error when you have a filename more than that; In new version, we increased it."
    - ## Did i saw this movie?: 
        This was the question i asked myself a lot. Fix it with a simple watched_time.