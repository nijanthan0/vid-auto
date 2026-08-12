import os
import re
import sys
import json
import asyncio
import hashlib
import argparse
import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
import PIL.Image

# Monkey-patch PIL.Image.ANTIALIAS to fix compatibility of older MoviePy with Pillow 10+
if not hasattr(PIL.Image, 'ANTIALIAS'):
    try:
        PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS
    except AttributeError:
        PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

# Load environment variables
load_dotenv()

# MoviePy imports with fallback compatibility for v1.x and v2.x
try:
    from moviepy import VideoFileClip, AudioFileClip, ImageClip, CompositeVideoClip, concatenate_videoclips, CompositeAudioClip
except ImportError:
    try:
        from moviepy.editor import VideoFileClip, AudioFileClip, ImageClip, CompositeVideoClip, concatenate_videoclips, CompositeAudioClip
    except ImportError as e:
        print(f"Error importing MoviePy. Please ensure moviepy is installed: {e}")
        sys.exit(1)

# List of common English stopwords and non-visual/structural words to filter out from search keywords
STOPWORDS = {
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your", "yours", 
    "yourself", "yourselves", "he", "him", "his", "himself", "she", "her", "hers", "herself", 
    "it", "its", "itself", "they", "them", "their", "theirs", "themselves", "what", "which", 
    "who", "whom", "this", "that", "these", "those", "am", "is", "are", "was", "were", "be", 
    "been", "being", "have", "has", "had", "having", "do", "does", "did", "doing", "a", "an", 
    "the", "and", "but", "if", "or", "because", "as", "until", "while", "of", "at", "by", "for", 
    "with", "about", "against", "between", "into", "through", "during", "before", "after", 
    "above", "below", "to", "from", "up", "down", "in", "out", "on", "off", "over", "under", 
    "again", "further", "then", "once", "here", "there", "when", "where", "why", "how", "all", 
    "any", "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not", 
    "only", "own", "same", "so", "than", "too", "very", "s", "t", "can", "will", "just", "don", 
    "should", "now", "would", "could", "shouldnt", "cant", "wont", "dont", "isnt", "arent",
    # Ordinals / Numbers
    "first", "second", "third", "fourth", "fifth", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten", "last", "next", "then", "step", "number",
    # Call-to-actions & Structural/Non-Visual Verbs and Nouns
    "subscribe", "like", "share", "follow", "comment", "video", "channel", "watch", "see",
    "look", "explore", "know", "ready", "secrets", "mysteries", "defy", "laws", "physics",
    "there", "here", "want", "need", "think", "tell", "say", "ask", "show", "help", "work",
    "seem", "try", "make", "take", "give", "find", "use", "go", "come", "get", "got", "let"
}

# --- MoviePy 1.x & 2.x API Compatibility Wrappers ---

def compat_resize(clip, width, height):
    """Resize a MoviePy clip (works with v1.x resize and v2.x resized)."""
    if hasattr(clip, "resized"):
        return clip.resized(width=width, height=height)
    elif hasattr(clip, "resize"):
        return clip.resize((width, height))
    return clip

def compat_crop(clip, x1, y1, width, height):
    """Crop a MoviePy clip centered (works with v1.x crop and v2.x cropped)."""
    if hasattr(clip, "cropped"):
        return clip.cropped(x1=x1, y1=y1, width=width, height=height)
    elif hasattr(clip, "crop"):
        return clip.crop(x1=x1, y1=y1, width=width, height=height)
    return clip

def compat_with_duration(clip, duration):
    """Set duration of a MoviePy clip (works with v1.x set_duration and v2.x with_duration)."""
    if hasattr(clip, "with_duration"):
        return clip.with_duration(duration)
    elif hasattr(clip, "set_duration"):
        return clip.set_duration(duration)
    return clip

def compat_with_audio(clip, audio):
    """Set audio of a MoviePy clip (works with v1.x set_audio and v2.x with_audio)."""
    if hasattr(clip, "with_audio"):
        return clip.with_audio(audio)
    elif hasattr(clip, "set_audio"):
        return clip.set_audio(audio)
    return clip

def compat_subclip(clip, start, end):
    """Slice a MoviePy clip (works with v1.x subclip and v2.x subclipped)."""
    if hasattr(clip, "subclipped"):
        return clip.subclipped(start, end)
    elif hasattr(clip, "subclip"):
        return clip.subclip(start, end)
    return clip

def compat_with_volume_scaled(clip, scale):
    """Set volume scale of a MoviePy clip (works with v1.x volumex and v2.x with_volume_scaled)."""
    if hasattr(clip, "with_volume_scaled"):
        return clip.with_volume_scaled(scale)
    elif hasattr(clip, "volumex"):
        return clip.volumex(scale)
    return clip

def loop_audio(clip, target_duration):
    """Loop audio clip manually to fit target duration."""
    if clip.duration >= target_duration:
        return compat_subclip(clip, 0, target_duration)
    
    clips = []
    current_duration = 0
    while current_duration < target_duration:
        clips.append(clip)
        current_duration += clip.duration
        
    try:
        from moviepy.audio.AudioClip import concatenate_audioclips
        concatenated = concatenate_audioclips(clips)
    except ImportError:
        try:
            from moviepy.editor import concatenate_audioclips
            concatenated = concatenate_audioclips(clips)
        except ImportError:
            return clip
            
    return compat_subclip(concatenated, 0, target_duration)

# ----------------------------------------------------

def get_hash(text):
    """Generate MD5 hash of string to use for file caching."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def clean_and_split_script(text):
    """Split text script into clean sentences."""
    # Split by periods, exclamation marks, or question marks followed by spaces or newlines
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    # Filter out empty or whitespace-only lines
    return [s.strip() for s in sentences if s.strip()]

def extract_keywords(sentence):
    """Extract descriptive keywords from a sentence for search queries."""
    # Remove special characters/punctuation
    cleaned = re.sub(r'[^\w\s]', '', sentence.lower())
    words = cleaned.split()
    
    # Filter stopwords and short terms
    filtered = [w for w in words if w not in STOPWORDS and len(w) > 2]
    
    if not filtered:
        # Fallback to all words if everything was filtered
        filtered = [w for w in words if len(w) > 1]
        
    if not filtered:
        return "abstract background"
        
    # Return first 3 descriptive terms
    return " ".join(filtered[:3])

async def generate_tts_file(text, voice, pitch, rate, output_path):
    """Generate audio using Edge TTS and save to output_path."""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, pitch=pitch, rate=rate)
    await communicate.save(output_path)

def run_tts(text, voice, pitch, rate, output_path):
    """Synchronous wrapper for edge-tts generation."""
    asyncio.run(generate_tts_file(text, voice, pitch, rate, output_path))

def fetch_pexels_video_url(keyword, pexels_key, orientation="portrait"):
    """Search Pexels API for a vertical stock video and return the download URL."""
    if not pexels_key:
        print("Warning: Pexels API Key not configured. Using color background fallbacks.")
        return None
        
    headers = {"Authorization": pexels_key}
    url = "https://api.pexels.com/videos/search"
    params = {
        "query": keyword,
        "per_page": 5,
        "orientation": orientation
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 401:
            print("Error: Pexels API Key is invalid.")
            return None
        elif response.status_code != 200:
            print(f"Error querying Pexels: {response.status_code} - {response.text}")
            return None
            
        data = response.json()
        videos = data.get("videos", [])
        if not videos:
            print(f"No portrait videos found for query: '{keyword}'. Trying general query...")
            # Try searching landscape videos as fallback (we will crop it anyway)
            params.pop("orientation", None)
            response = requests.get(url, headers=headers, params=params, timeout=10)
            data = response.json()
            videos = data.get("videos", [])
            
        if not videos:
            return None
            
        # Select best vertical MP4 video link
        for video in videos:
            video_files = video.get("video_files", [])
            # Priority 1: HD mp4 vertical
            for f in video_files:
                if f.get("file_type") == "video/mp4":
                    w = f.get("width") or 0
                    h = f.get("height") or 0
                    if h > w and f.get("quality") == "hd":
                        return f.get("link")
            # Priority 2: Standard mp4 vertical
            for f in video_files:
                if f.get("file_type") == "video/mp4":
                    w = f.get("width") or 0
                    h = f.get("height") or 0
                    if h > w:
                        return f.get("link")
            # Priority 3: HD mp4 (landscape, we crop later)
            for f in video_files:
                if f.get("file_type") == "video/mp4" and f.get("quality") == "hd":
                    return f.get("link")
            # Priority 4: Any mp4
            for f in video_files:
                if f.get("file_type") == "video/mp4":
                    return f.get("link")
                    
        return None
    except Exception as e:
        print(f"Failed to fetch stock video from Pexels: {e}")
        return None

def download_file(url, output_path):
    """Download a file with streaming chunk writes."""
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return True
    except Exception as e:
        print(f"Error downloading file: {e}")
        if os.path.exists(output_path):
            os.remove(output_path)
        return False

def get_font_path(font_name):
    """Locate a true-type font on the system or download one from Google Fonts as a fallback."""
    import platform
    system = platform.system()
    
    if os.path.exists(font_name):
        return font_name
        
    paths = []
    if system == "Darwin":  # macOS
        paths = [
            f"/Library/Fonts/{font_name}.ttf",
            f"/Library/Fonts/{font_name}.otf",
            f"/System/Library/Fonts/{font_name}.ttf",
            f"/System/Library/Fonts/Supplemental/{font_name}.ttf",
            f"/System/Library/Fonts/Supplemental/{font_name}.otf",
        ]
    elif system == "Windows":
        paths = [
            f"C:\\Windows\\Fonts\\{font_name}.ttf",
            f"C:\\Windows\\Fonts\\{font_name}.otf",
        ]
    elif system == "Linux":
        paths = [
            f"/usr/share/fonts/truetype/dejavu/{font_name}.ttf",
            f"/usr/share/fonts/truetype/freefont/{font_name}Bold.ttf",
            f"/usr/share/fonts/TTF/{font_name}.ttf",
        ]
        
    for p in paths:
        if os.path.exists(p):
            return p
            
    # Fallback to downloading Montserrat-ExtraBold from Google Fonts
    font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    os.makedirs(font_dir, exist_ok=True)
    fallback_path = os.path.join(font_dir, "Montserrat-ExtraBold.ttf")
    
    if not os.path.exists(fallback_path):
        print(f"Font '{font_name}' not found. Downloading Montserrat-ExtraBold from Google Fonts...")
        try:
            url = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-ExtraBold.ttf"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                with open(fallback_path, "wb") as f:
                    f.write(r.content)
                print("Font downloaded successfully.")
                return fallback_path
        except Exception as e:
            print(f"Could not download font: {e}. Using PIL default font.")
            
    if os.path.exists(fallback_path):
        return fallback_path
        
    return None

def create_subtitle_overlay(text, width, height, font_path, font_size, text_color, outline_color, outline_width, y_pos_pct, output_path):
    """Draw text subtitles on a transparent frame using Pillow and save as a PNG."""
    # Create transparent image
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Load Font
    if font_path:
        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()
        
    # Wrap text to fit screen width (85% of screen width)
    words = text.split()
    lines = []
    current_line = []
    max_line_width = int(width * 0.85)
    
    for word in words:
        current_line.append(word)
        test_line = " ".join(current_line)
        
        # Check text width
        if hasattr(draw, "textbbox"):
            w = draw.textbbox((0, 0), test_line, font=font)[2]
        else:
            w = draw.textsize(test_line, font=font)[0]
            
        if w > max_line_width:
            current_line.pop()
            lines.append(" ".join(current_line))
            current_line = [word]
            
    if current_line:
        lines.append(" ".join(current_line))
        
    # Calculate Y-positioning offsets
    y_offset = int(height * y_pos_pct)
    
    line_heights = []
    for line in lines:
        if hasattr(draw, "textbbox"):
            bbox = draw.textbbox((0, 0), line, font=font)
            line_heights.append(bbox[3] - bbox[1])
        else:
            line_heights.append(draw.textsize(line, font=font)[1])
            
    total_text_height = sum(line_heights) + (len(lines) - 1) * 12
    current_y = y_offset - total_text_height // 2
    
    # Draw outline + text
    for i, line in enumerate(lines):
        if hasattr(draw, "textbbox"):
            w = draw.textbbox((0, 0), line, font=font)[2]
        else:
            w = draw.textsize(line, font=font)[0]
        x = (width - w) // 2
        
        # Outline (draws the text in outline_color offset in circle)
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx*dx + dy*dy <= outline_width*outline_width:
                    draw.text((x + dx, current_y + dy), line, font=font, fill=tuple(outline_color))
                    
        # Main text
        draw.text((x, current_y), line, font=font, fill=tuple(text_color))
        current_y += line_heights[i] + 12
        
    img.save(output_path)
    return output_path

def create_solid_color_fallback(width, height, color, output_path):
    """Create a dark fallback image if no stock video is found."""
    # Create simple dark solid color
    img = Image.new("RGB", (width, height), color)
    img.save(output_path)
    return output_path

def crop_and_resize_clip(clip, target_w, target_h):
    """Crop and resize a MoviePy clip to fit vertical aspect ratio (1080x1920)."""
    clip_w, clip_h = clip.size
    
    # Calculate scale factor to completely cover the target area
    scale_x = target_w / clip_w
    scale_y = target_h / clip_h
    scale = max(scale_x, scale_y)
    
    new_w = int(clip_w * scale)
    new_h = int(clip_h * scale)
    
    # Resize first using compatibility helper
    resized = compat_resize(clip, new_w, new_h)
    
    # Crop centered using compatibility helper
    x1 = (new_w - target_w) // 2
    y1 = (new_h - target_h) // 2
    cropped = compat_crop(resized, x1=x1, y1=y1, width=target_w, height=target_h)
    
    return cropped

def match_duration(clip, target_duration):
    """Match visual clip duration to TTS audio duration, looping if too short."""
    if clip.duration >= target_duration:
        return compat_subclip(clip, 0, target_duration)
    else:
        # Loop fallback
        try:
            from moviepy.video.fx.all import loop
            return loop(clip, duration=target_duration)
        except Exception:
            # Manual chaining loop fallback if FX fails
            n_loops = int(target_duration // clip.duration) + 1
            clips = [clip] * n_loops
            chained = concatenate_videoclips(clips)
            return compat_subclip(chained, 0, target_duration)

def assemble_scenes(plan, config, temp_dir, cache_dir):
    """Assemble all individual scene plans into one continuous vertical video."""
    width = config["width"]
    height = config["height"]
    voice = config["voice"]
    pitch = config["pitch"]
    rate = config["rate"]
    font_path = get_font_path(config["subtitle_font"])
    font_size = config["subtitle_font_size"]
    text_color = config["subtitle_color"]
    outline_color = config["subtitle_outline_color"]
    outline_width = config["subtitle_outline_width"]
    y_pos_pct = config["subtitles_y_position"]
    pexels_key = os.getenv("PEXELS_API_KEY") or config.get("pexels_api_key")
    
    scene_clips = []
    
    for idx, scene in enumerate(plan):
        text = scene["text"]
        keyword = scene["keyword"]
        scene_id = idx + 1
        print(f"\n--- Processing Scene {scene_id}/{len(plan)}: '{text[:40]}...' ---")
        
        # 1. TTS Generation
        audio_filename = f"tts_{get_hash(text + voice + pitch + rate)}.mp3"
        audio_path = os.path.join(cache_dir, audio_filename)
        
        if not os.path.exists(audio_path):
            print(f"Generating TTS voiceover...")
            run_tts(text, voice, pitch, rate, audio_path)
        else:
            print("Loaded TTS voiceover from cache.")
            
        audio_clip = AudioFileClip(audio_path)
        duration = audio_clip.duration
        print(f"Scene duration: {duration:.2f}s")
        
        # 2. Stock Visual Selection & Download (supports string or list of strings)
        keywords_list = keyword if isinstance(keyword, list) else [keyword]
        sub_duration = duration / len(keywords_list)
        visual_sub_clips = []
        
        for k_idx, kw in enumerate(keywords_list):
            kw_cleaned = kw.strip()
            sub_clip = None
            video_url = None
            
            if pexels_key:
                video_url = fetch_pexels_video_url(kw_cleaned, pexels_key, "portrait")
                
            if video_url:
                video_filename = f"pexels_{get_hash(video_url)}.mp4"
                video_path = os.path.join(cache_dir, video_filename)
                
                if not os.path.exists(video_path):
                    print(f"Downloading stock video for query '{kw_cleaned}'...")
                    success = download_file(video_url, video_path)
                    if not success:
                        video_path = None
                else:
                    print(f"Loaded stock video for '{kw_cleaned}' from cache.")
                    
                if video_path and os.path.exists(video_path):
                    try:
                        sub_clip = VideoFileClip(video_path)
                    except Exception as e:
                        print(f"Failed to load downloaded video clip: {e}")
                        sub_clip = None
            else:
                if pexels_key:
                    print(f"No matching video on Pexels for '{kw_cleaned}'.")
                    
            # Fallback if download failed or key is missing
            if not sub_clip:
                fallback_img_path = os.path.join(temp_dir, f"fallback_{scene_id}_{k_idx}.png")
                # Generate deep charcoal/grey style background
                create_solid_color_fallback(width, height, (25, 25, 25), fallback_img_path)
                sub_clip = ImageClip(fallback_img_path)
                sub_clip = compat_with_duration(sub_clip, sub_duration)
                
            # Match size and duration
            sub_clip = crop_and_resize_clip(sub_clip, width, height)
            sub_clip = match_duration(sub_clip, sub_duration)
            visual_sub_clips.append(sub_clip)
            
        # Concatenate visual sub-clips for this scene
        if len(visual_sub_clips) == 1:
            visual_clip = visual_sub_clips[0]
        else:
            visual_clip = concatenate_videoclips(visual_sub_clips, method="compose")
            
        # Add audio using compat helper
        visual_clip = compat_with_audio(visual_clip, audio_clip)
        
        # 3. Subtitle Overlay
        subtitle_img_path = os.path.join(temp_dir, f"subtitle_{scene_id}.png")
        create_subtitle_overlay(
            text=text,
            width=width,
            height=height,
            font_path=font_path,
            font_size=font_size,
            text_color=text_color,
            outline_color=outline_color,
            outline_width=outline_width,
            y_pos_pct=y_pos_pct,
            output_path=subtitle_img_path
        )
        
        subtitle_clip = ImageClip(subtitle_img_path)
        # Use compat helper for setting duration
        subtitle_clip = compat_with_duration(subtitle_clip, duration)
        
        # Overlay subtitle on top
        scene_clip = CompositeVideoClip([visual_clip, subtitle_clip])
        scene_clips.append(scene_clip)
        
    # Concatenate all scenes into final video
    print("\nStitching all scenes together...")
    final_video = concatenate_videoclips(scene_clips, method="compose")
    return final_video

def main():
    parser = argparse.ArgumentParser(description="Generate vertical short/reel videos from scripts.")
    parser.add_argument("--script", type=str, help="Path to input text script file (splits by sentence)")
    parser.add_argument("--plan", type=str, help="Path to a pre-defined script JSON plan containing texts and keywords")
    parser.add_argument("--dry-run", action="store_true", help="Generate a script plan JSON file and stop without building the video")
    parser.add_argument("--music", type=str, help="Path to optional background music file (e.g. bgm.mp3)")
    parser.add_argument("--output", type=str, default="output.mp4", help="Name of output video file")
    args = parser.parse_args()
    
    # Load configuration
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = json.load(f)
    else:
        print("config.json not found! Using default configuration.")
        config = {
            "aspect_ratio": "9:16",
            "width": 1080,
            "height": 1920,
            "voice": "en-US-AndrewNeural",
            "pitch": "-30Hz",
            "rate": "+0%",
            "subtitle_font": "Impact",
            "subtitle_font_size": 70,
            "subtitle_color": [255, 255, 0],
            "subtitle_outline_color": [0, 0, 0],
            "subtitle_outline_width": 6,
            "subtitles_y_position": 0.7,
            "pexels_api_key": ""
        }
        
    # Directories setup
    base_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(base_dir, ".temp")
    cache_dir = os.path.join(base_dir, ".cache")
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)
    
    plan = []
    
    # Check inputs
    if args.plan:
        with open(args.plan, "r") as f:
            plan = json.load(f)
    elif args.script:
        if not os.path.exists(args.script):
            print(f"Error: Script file '{args.script}' does not exist.")
            sys.exit(1)
        with open(args.script, "r", encoding="utf-8") as f:
            script_content = f.read()
            
        sentences = clean_and_split_script(script_content)
        for s in sentences:
            plan.append({
                "text": s,
                "keyword": extract_keywords(s)
            })
            
        # Save plan
        plan_out_path = os.path.join(base_dir, "script_plan.json")
        with open(plan_out_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)
        print(f"Generated video build plan: '{plan_out_path}'")
        
        if args.dry_run:
            print("Dry run completed. Edit the keywords in 'script_plan.json' and build using: ")
            print(f"python generator.py --plan script_plan.json --output {args.output}")
            sys.exit(0)
    else:
        print("Error: You must provide either --script or --plan.")
        parser.print_help()
        sys.exit(1)
        
    if not plan:
        print("Error: Empty build plan.")
        sys.exit(1)
        
    # Build Video
    try:
        final_clip = assemble_scenes(plan, config, temp_dir, cache_dir)
        
        # Mix background music if provided
        if args.music and os.path.exists(args.music):
            print(f"Mixing background music '{args.music}'...")
            try:
                bgm = AudioFileClip(args.music)
                bgm_looped = loop_audio(bgm, final_clip.duration)
                bgm_scaled = compat_with_volume_scaled(bgm_looped, 0.12) # 12% volume
                
                # Combine original voiceover audio and scaled background music
                mixed_audio = CompositeAudioClip([final_clip.audio, bgm_scaled])
                final_clip = compat_with_audio(final_clip, mixed_audio)
                print("Background music mixed successfully.")
            except Exception as e:
                print(f"Warning: Failed to mix background music: {e}. Proceeding without it.")
        elif args.music:
            print(f"Warning: Background music file '{args.music}' not found. Proceeding without BGM.")

        print(f"\nRendering final video: {args.output}...")
        final_clip.write_videofile(
            args.output,
            fps=30,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=os.path.join(temp_dir, "temp-audio.m4a"),
            remove_temp=True
        )
        print("\n=== Success! Video created successfully! ===")
    except Exception as e:
        print(f"\nAn error occurred during video generation: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup temporary files
        print("Cleaning up temp files...")
        for filename in os.listdir(temp_dir):
            file_path = os.path.join(temp_dir, filename)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
            except Exception as e:
                print(f"Failed to delete {file_path}: {e}")

if __name__ == "__main__":
    main()
