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

Windows"
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
mkdir D:\storage_server\media, D:\storage_server\databases, D:\storage_server\logs -Force
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