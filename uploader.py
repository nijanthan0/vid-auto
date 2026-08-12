import os
import sys
import time
import pickle
import argparse
import requests
import subprocess
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()

# --- Instagram Reels Uploader Helpers (Graph API) ---

def get_git_remote_url():
    """Retrieve the Git remote origin URL."""
    try:
        url = subprocess.check_output(["git", "config", "--get", "remote.origin.url"], text=True).strip()
        return url
    except Exception:
        return None

def upload_to_github(video_path):
    """Commit and push the video to the current GitHub repository and return its raw URL."""
    print("🐙 Attempting to push video to GitHub for public hosting...")
    
    # 1. Check if git is initialized
    if not os.path.exists(".git"):
        print("Initializing git repository...")
        try:
            subprocess.run(["git", "init"], check=True)
            subprocess.run(["git", "branch", "-M", "main"], check=True)
        except Exception as e:
            print(f"Failed to initialize git repository: {e}")
            return None
        
    # 2. Get remote URL
    remote_url = get_git_remote_url()
    if not remote_url:
        print("\n⚠️ Git remote origin not found!")
        print("To host the video automatically on GitHub, link this folder to your repository:")
        print("  1. Create a new repository named 'Vid-auto' on your GitHub (https://github.com/new).")
        print("  2. Run the following command in this folder:")
        print("     git remote add origin https://github.com/<your-username>/Vid-auto.git")
        print("  3. Run this script again.")
        return None
        
    # Parse username and repo from remote URL
    # Supports formats:
    # https://github.com/username/repo.git
    # git@github.com:username/repo.git
    parts = remote_url.replace(".git", "").replace(":", "/").split("/")
    if len(parts) >= 2:
        username = parts[-2]
        repo = parts[-1]
    else:
        print(f"Error parsing remote URL: {remote_url}")
        return None
        
    # Get active branch name
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
    except Exception:
        branch = "main"
        
    # 3. Add, commit, and push
    try:
        print(f"Staging '{video_path}'...")
        subprocess.run(["git", "add", video_path], check=True)
        
        # Configure local bot credentials if not set
        try:
            subprocess.run(["git", "config", "user.name", "VidAutoBot"], check=True)
            subprocess.run(["git", "config", "user.email", "bot@example.com"], check=True)
        except Exception:
            pass
        
        print("Committing video asset...")
        # Ignore check-in commits if no changes
        subprocess.run(["git", "commit", "-m", f"Upload {video_path} [skip ci]"], check=True)
        
        print("Pushing to GitHub remote...")
        subprocess.run(["git", "push", "origin", branch], check=True)
        
        # 4. Construct raw GitHub URL
        # Format: https://raw.githubusercontent.com/{username}/{repo}/{branch}/{video_path}
        raw_url = f"https://raw.githubusercontent.com/{username}/{repo}/{branch}/{video_path}"
        print(f"✅ Video hosted successfully on GitHub! Raw URL: {raw_url}")
        return raw_url
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Git automation command failed: {e}")
        return None


def upload_to_tmpfiles(file_path):
    """Upload file temporarily to tmpfiles.org to get a direct public URL for Meta API."""
    print("Uploading video to temporary hosting (tmpfiles.org)...")
    url = "https://tmpfiles.org/api/v1/upload"
    try:
        with open(file_path, "rb") as f:
            files = {"file": f}
            res = requests.post(url, files=files, timeout=60)
            if res.status_code == 200:
                data = res.json()
                upload_url = data.get("data", {}).get("url")
                if upload_url:
                    # Convert to direct download link:
                    # Replace 'https://tmpfiles.org/' with 'https://tmpfiles.org/dl/'
                    direct_url = upload_url.replace("https://tmpfiles.org/", "https://tmpfiles.org/dl/")
                    print(f"Temporary direct video URL: {direct_url}")
                    return direct_url
            print(f"tmpfiles.org upload failed: {res.text}")
    except Exception as e:
        print(f"Error uploading to tmpfiles.org: {e}")
    return None

def get_instagram_business_account_id(token):
    """Retrieve Instagram Business Account ID from Page Access Token."""
    # Check if the token points to a Page directly
    url = "https://graph.facebook.com/v18.0/me"
    params = {"fields": "instagram_business_account", "access_token": token}
    try:
        res = requests.get(url, params=params, timeout=10).json()
        if "instagram_business_account" in res:
            return res["instagram_business_account"]["id"]
    except Exception:
        pass
        
    # Check if the token is a User Token and fetch accounts
    url_accounts = "https://graph.facebook.com/v18.0/me/accounts"
    params_accounts = {"access_token": token}
    try:
        res_accounts = requests.get(url_accounts, params=params_accounts, timeout=10).json()
        pages = res_accounts.get("data", [])
        for page in pages:
            page_id = page.get("id")
            if page_id:
                url_page = f"https://graph.facebook.com/v18.0/{page_id}"
                params_page = {"fields": "instagram_business_account", "access_token": token}
                res_page = requests.get(url_page, params=params_page, timeout=10).json()
                if "instagram_business_account" in res_page:
                    return res_page["instagram_business_account"]["id"]
    except Exception as e:
        print(f"Error fetching account list from token: {e}")
        
    return None

def upload_to_instagram_graph(video_path, caption, access_token, custom_video_url=None):
    """Publish a Reel using Meta Graph API with temporary tmpfiles.org hosting or a custom video URL."""
    ig_user_id = get_instagram_business_account_id(access_token)
    if not ig_user_id:
        print("Error: Could not retrieve Instagram Business Account ID from access token.")
        print("Ensure the token has 'instagram_basic' and 'instagram_content_publish' permissions,")
        print("and that your Instagram Business account is linked to your Facebook Page.")
        return False
        
    print(f"Authenticated Instagram Business Account: {ig_user_id}")
    
    # 1. Get public video URL
    if custom_video_url:
        public_url = custom_video_url
        print(f"Using provided custom direct video URL: {public_url}")
    else:
        # Try GitHub first
        public_url = upload_to_github(video_path)
        if not public_url:
            print("GitHub upload was not configured. Falling back to temporary hosting...")
            public_url = upload_to_tmpfiles(video_path)
        
    if not public_url:
        print("Error: Failed to obtain video URL for Meta API.")
        return False
        
    # 2. Create media container
    print("Creating media container on Instagram...")
    url = f"https://graph.facebook.com/v18.0/{ig_user_id}/media"
    payload = {
        "media_type": "REELS",
        "video_url": public_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": access_token
    }
    
    try:
        res = requests.post(url, data=payload, timeout=15).json()
        if "id" not in res:
            print(f"Error creating Reels container: {res}")
            return False
        container_id = res["id"]
        print(f"Media container created successfully. ID: {container_id}")
        
        # 3. Poll for status
        print("Waiting for Instagram to process the video (polling status)...")
        status_url = f"https://graph.facebook.com/v18.0/{container_id}"
        status_params = {"fields": "status_code,status", "access_token": access_token}
        
        for attempt in range(30):  # Poll for up to 5 minutes (30 * 10 seconds)
            time.sleep(10)
            status_res = requests.get(status_url, params=status_params, timeout=10).json()
            status = status_res.get("status_code")
            print(f"Processing status: {status}")
            if status == "FINISHED":
                break
            elif status == "ERROR":
                print(f"Error processing video on Instagram: {status_res}")
                return False
        else:
            print("Timed out waiting for Instagram processing.")
            return False
            
        # 4. Publish Container
        print("Publishing Reel to feed...")
        publish_url = f"https://graph.facebook.com/v18.0/{ig_user_id}/media_publish"
        publish_payload = {
            "creation_id": container_id,
            "access_token": access_token
        }
        
        publish_res = requests.post(publish_url, data=publish_payload, timeout=15).json()
        if "id" in publish_res:
            print("=== Instagram Success! Reels published via Graph API! ===")
            print(f"Media ID: {publish_res['id']}")
            return True
        else:
            print(f"Error publishing media: {publish_res}")
            return False
            
    except Exception as e:
        print(f"Graph API Upload failed: {e}")
        return False


def upload_to_instagram(video_path, caption, custom_video_url=None, force_login=False):
    """Log in and upload video to Instagram Reels (uses Graph API token if available, otherwise instagrapi)."""
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    
    if access_token and not force_login:
        print("Using Instagram Graph API token for upload...")
        return upload_to_instagram_graph(video_path, caption, access_token, custom_video_url)
        
    print("Instagram Access Token not found. Falling back to username/password login...")
    username = os.getenv("INSTAGRAM_USERNAME")
    password = os.getenv("INSTAGRAM_PASSWORD")
    
    if not username or not password:
        print("Error: Neither INSTAGRAM_ACCESS_TOKEN nor (INSTAGRAM_USERNAME/PASSWORD) is set in your .env file.")
        return False
        
    try:
        from instagrapi import Client
        cl = Client()
        
        # Load cached session settings if they exist to avoid frequent login requests
        session_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instagram_session.json")
        if os.path.exists(session_file):
            print("Attempting login via cached session settings...")
            try:
                cl.load_settings(session_file)
                cl.login(username, password)
            except Exception:
                print("Session expired. Logging in with username and password...")
                cl.login(username, password)
                cl.dump_settings(session_file)
        else:
            print("Logging in with username and password...")
            cl.login(username, password)
            cl.dump_settings(session_file)
            
        print(f"Session loaded successfully. Uploading '{video_path}' to Reels...")
        
        # instagrapi direct upload method for Reels
        media = cl.clip_upload(video_path, caption)
        print(f"=== Instagram Success! Reels posted successfully! ===")
        print(f"Media ID: {media.pk}")
        return True
    except Exception as e:
        print(f"Error uploading to Instagram: {e}")
        return False


# --- YouTube Shorts Uploader using Official API ---

def get_youtube_service():
    """Authenticate and return the YouTube v3 API service."""
    try:
        from googleapiclient.discovery import build
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
    except ImportError:
        print("Error: Google API client libraries not installed. Run 'pip install -r requirements.txt'")
        sys.exit(1)
        
    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
    base_dir = os.path.dirname(os.path.abspath(__file__))
    token_path = os.path.join(base_dir, "youtube_token.pickle")
    secrets_path = os.path.join(base_dir, "client_secrets.json")
    
    creds = None
    if os.path.exists(token_path):
        with open(token_path, "rb") as token:
            creds = pickle.load(token)
            
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing YouTube credentials...")
            creds.refresh(Request())
        else:
            if not os.path.exists(secrets_path):
                print("\nError: 'client_secrets.json' not found!")
                print("To upload to YouTube, please follow these steps:")
                print("1. Go to Google Cloud Console (https://console.cloud.google.com).")
                print("2. Create a project and enable the 'YouTube Data API v3'.")
                print("3. Configure the OAuth Consent Screen (Internal or External test mode).")
                print("4. Go to credentials -> Create Credentials -> OAuth Client ID (Desktop Application).")
                print("5. Download the JSON credentials file and rename it to 'client_secrets.json' in this folder.")
                sys.exit(1)
            print("Prompting browser for YouTube OAuth Login...")
            flow = InstalledAppFlow.from_client_secrets_file(secrets_path, SCOPES)
            creds = flow.run_local_server(port=0)
            
        with open(token_path, "wb") as token:
            pickle.dump(creds, token)
            
    return build("youtube", "v3", credentials=creds)

def upload_to_youtube(video_path, title, description="Uploaded via Automated Video Generator"):
    """Authenticate and upload video to YouTube Shorts."""
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        print("Error: Google API client libraries not installed.")
        return False
        
    # Ensure title contains "#Shorts" tag for correct categorization
    if "#shorts" not in title.lower():
        title = f"{title} #Shorts"
        
    try:
        youtube = get_youtube_service()
        
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": "22"  # People & Blogs category ID
            },
            "status": {
                "privacyStatus": "public",  # Can be 'public', 'private', or 'unlisted'
                "selfDeclaredMadeForKids": False
            }
        }
        
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )
        
        print(f"Uploading '{video_path}' to YouTube Shorts...")
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"Upload progress: {int(status.progress() * 100)}%...")
                
        print(f"=== YouTube Success! Video uploaded successfully! ===")
        print(f"Video ID: {response['id']}")
        print(f"Link: https://www.youtube.com/watch?v={response['id']}")
        return True
    except Exception as e:
        print(f"Error uploading to YouTube: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Upload vertical videos to YouTube Shorts and Instagram Reels.")
    parser.add_argument("--video", type=str, required=True, help="Path to the video file to upload")
    parser.add_argument("--title", type=str, required=True, help="Title of the video (used as Instagram caption & YouTube title)")
    parser.add_argument("--desc", type=str, default="Check out this auto-generated content!", help="YouTube Video Description")
    parser.add_argument("--video-url", type=str, default=None, help="Optional direct public URL of the video (bypasses automatic temp hosting)")
    parser.add_argument("--force-login", action="store_true", help="Force direct username/password login method even if access token is configured")
    parser.add_argument("--platform", type=str, choices=["all", "instagram", "youtube"], default="all",
                        help="Platform to publish the video to (all, instagram, or youtube)")
    args = parser.parse_args()
    
    if not os.path.exists(args.video):
        print(f"Error: Video file '{args.video}' does not exist.")
        sys.exit(1)
        
    success_ig = False
    success_yt = False
    
    if args.platform in ["all", "instagram"]:
        print("\n--- Posting to Instagram Reels ---")
        success_ig = upload_to_instagram(args.video, args.title, args.video_url, args.force_login)
        
    if args.platform in ["all", "youtube"]:
        print("\n--- Posting to YouTube Shorts ---")
        success_yt = upload_to_youtube(args.video, args.title, args.desc)
        
    print("\n--- Upload Session Summary ---")
    if args.platform == "all":
        print(f"Instagram: {'SUCCESS' if success_ig else 'FAILED'}")
        print(f"YouTube: {'SUCCESS' if success_yt else 'FAILED'}")
    elif args.platform == "instagram":
        print(f"Instagram: {'SUCCESS' if success_ig else 'FAILED'}")
    elif args.platform == "youtube":
        print(f"YouTube: {'SUCCESS' if success_yt else 'FAILED'}")

if __name__ == "__main__":
    main()
