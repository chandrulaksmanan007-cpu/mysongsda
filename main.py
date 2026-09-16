import os
import uuid
import asyncio
import time
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
from fastapi.staticfiles import StaticFiles

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMP_DIR = "temp_downloads"
os.makedirs(TEMP_DIR, exist_ok=True)

class InfoRequest(BaseModel):
    url: str

class DownloadRequest(BaseModel):
    url: str
    format_type: str # "mp4" or "mp3"
    quality: str # "1080", "720", "480", "best"

def cleanup_temp_files():
    """Background task to delete files older than 15 minutes."""
    now = time.time()
    for filename in os.listdir(TEMP_DIR):
        filepath = os.path.join(TEMP_DIR, filename)
        if os.path.isfile(filepath):
            # Check if file is older than 15 minutes (900 seconds)
            if now - os.path.getmtime(filepath) > 900:
                try:
                    os.remove(filepath)
                    print(f"Deleted old temp file: {filepath}")
                except Exception as e:
                    print(f"Error deleting old temp file {filepath}: {e}")

@app.post("/api/info")
async def get_info(request: InfoRequest):
    url = request.url
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            title = info.get('title', 'Unknown Title')
            thumbnail = info.get('thumbnail', '')
            
            # Extract resolutions
            resolutions = set()
            formats = info.get('formats', [])
            for f in formats:
                if f.get('vcodec') != 'none' and f.get('height'):
                    resolutions.add(f.get('height'))
            
            res_list = sorted(list(resolutions), reverse=True)
            available_resolutions = [str(r) for r in res_list if r in [1080, 720, 480]]
            if 'best' not in available_resolutions:
                 available_resolutions.insert(0, 'best')
            
            return {
                "title": title,
                "thumbnail": thumbnail,
                "resolutions": available_resolutions
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/download")
async def download_media(request: DownloadRequest, background_tasks: BackgroundTasks):
    url = request.url
    format_type = request.format_type
    quality = request.quality
    
    if format_type not in ["mp4", "mp3"]:
        raise HTTPException(status_code=400, detail="Invalid format_type")

    file_id = str(uuid.uuid4())
    output_template = os.path.join(TEMP_DIR, f"{file_id}.%(ext)s")
    
    ydl_opts = {
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
    }

    if format_type == "mp4":
        if quality == "best":
            ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        else:
            try:
                height = int(quality)
                ydl_opts['format'] = f'bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={height}][ext=mp4]/best'
            except ValueError:
                ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        ydl_opts['merge_output_format'] = 'mp4'
    elif format_type == "mp3":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'video')
            # Sanitize title for filename
            safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
            if not safe_title:
                safe_title = "download"
            
            ext = "mp3" if format_type == "mp3" else "mp4"
            
            # Find the output file, yt-dlp might have changed the extension during muxing/conversion
            # But with our setup it should match the requested format_type.
            expected_filepath = os.path.join(TEMP_DIR, f"{file_id}.{ext}")
            
            if not os.path.exists(expected_filepath):
                 # Try to find any file starting with this UUID just in case
                 files_starting_with_id = [f for f in os.listdir(TEMP_DIR) if f.startswith(file_id)]
                 if files_starting_with_id:
                     expected_filepath = os.path.join(TEMP_DIR, files_starting_with_id[0])
                 else:
                     raise HTTPException(status_code=500, detail="File processing failed.")

            # Queue cleanup before returning
            background_tasks.add_task(cleanup_temp_files)

            return FileResponse(
                path=expected_filepath,
                filename=f"{safe_title}.{ext}",
                media_type="application/octet-stream"
            )
            
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Mount static files at the root
app.mount("/", StaticFiles(directory="static", html=True), name="static")
