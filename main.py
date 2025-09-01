import os
import json
import asyncio
import re
import math
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List
from fastapi import FastAPI, HTTPException, BackgroundTasks
import httpx
from pydantic import BaseModel, HttpUrl, Field
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("facebook_scorer.log")]
)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="Facebook Social Scoring API",
    description="API for calculating social credit scores from Facebook profiles",
    version="1.0.0",
    docs_url="/docs"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Constants
DEFAULT_MIN_SCORE = 300
DEFAULT_MAX_SCORE = 850
MAX_RETRIES = 3
REQUEST_TIMEOUT = 30000
CALLBACK_TIMEOUT = 10
COOKIE_PATH = Path("cookies/facebook_cookies.json")  


# Scoring weights
WEIGHTS = {
    'verification': 0.15,
    'followers': 0.30,
    'engagement': 0.25,
    'completeness': 0.15,
    'activity': 0.15
}

class SocialMediaRequest(BaseModel):
    social_media: str
    username: str

class ScoreRequest(BaseModel):
    type: str
    data: List[SocialMediaRequest]

class CentralScoreRequest(BaseModel):
    fayda_number: str
    requests: List[ScoreRequest]
    callbackUrl: Optional[HttpUrl] = None

class ScoreBreakdownItem(BaseModel):
    value: float
    max: float

class SocialScoreResponse(BaseModel):
    fayda_number: str
    score: int = Field(..., ge=300, le=850)
    score_range: str = "300-850"
    risk_level: str
    score_breakdown: Dict[str, ScoreBreakdownItem]
    timestamp: str
    type: str = "SOCIAL_SCORE"

class CentralScoreResponse(BaseModel):
    fayda_number: str
    combined_scores: Dict[str, SocialScoreResponse]

async def load_cookies() -> List[Dict]:
    if COOKIE_PATH.exists():
        try:
            with open(COOKIE_PATH, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Cookie load failed: {str(e)}")
    return []

async def save_cookies(cookies: List[Dict]):
    try:
        with open(COOKIE_PATH, 'w') as f:
            json.dump(cookies, f)
    except Exception as e:
        logger.error(f"Cookie save failed: {str(e)}")

async def ensure_authenticated(page):
    cookies = await load_cookies()
    if cookies:
        await page.context.add_cookies(cookies)
        await page.goto("https://facebook.com", timeout=15000)
        if "login" not in page.url.lower():
            return True

    try:
        await page.goto("https://facebook.com/login", timeout=15000)
        await page.fill("#email", os.getenv("FACEBOOK_EMAIL"))
        await page.fill("#pass", os.getenv("FACEBOOK_PASSWORD"))
        await page.click("button[name='login']")  # safer than #loginbutton

        # Option 1: wait for account menu (stable)
        await page.wait_for_selector("[aria-label='Account']", timeout=20000)

        # Option 2 (backup): check that we’re not on login anymore
        if "login" in page.url.lower():
            raise Exception("Still on login page, login failed")

        await save_cookies(await page.context.cookies())
        return True
    except Exception as e:
        logger.error(f"Login failed: {str(e)}")
        return False


async def fetch_profile(username: str) -> Optional[str]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        
        try:
            if not await ensure_authenticated(page):
                raise HTTPException(503, "Authentication failed")
            
            for url in [
                f"https://facebook.com/{username}",
                f"https://facebook.com/profile.php?id={username}"
            ]:
                try:
                    await page.goto(url, timeout=REQUEST_TIMEOUT)
                    if "content isn't available" not in (await page.content()).lower():
                        await asyncio.sleep(2)
                        return await page.content()
                except Exception as e:
                    logger.warning(f"Attempt failed: {str(e)}")
        finally:
            await browser.close()
    return None

def safe_divide(a: float, b: float) -> float:
    return a / b if b else 0.0

def get_risk_level(score: int) -> str:
    if score >= 750: return "Very Low Risk"
    if score >= 650: return "Low Risk"
    if score >= 550: return "Medium Risk"
    if score >= 450: return "High Risk"
    return "Very High Risk"

def determine_tier(followers: int, is_verified: bool) -> str:
    if is_verified:
        if followers >= 5000000: return "Elite"
        if followers >= 500000: return "Premium"
        return "Standard"
    
    if followers >= 10000000: return "Elite"
    if followers >= 1000000: return "Premium"
    if followers >= 100000: return "Standard"
    return "Basic"

def scale_to_range(raw_score: float, min_raw=0, max_raw=100) -> int:
    normalized = max(0, min(1, (raw_score - min_raw) / (max_raw - min_raw)))
    return int(DEFAULT_MIN_SCORE + (DEFAULT_MAX_SCORE - DEFAULT_MIN_SCORE) * normalized)

def parse_facebook_html(html: str, username: str) -> Dict:
    profile = {
        "username": username,
        "is_verified": False,
        "followers": 10000,
        "likes": 10000,
        "posts_count": 10,
        "engagement_rate": 0.3,
        "bio_length": 100,
        "has_profile_photo": True,
        "has_cover_photo": True,
    }

    try:
        profile['is_verified'] = any(
            re.search(pattern, html, re.IGNORECASE)
            for pattern in [
                r'"is_verified":\s*true',
                r'verified_badge',
                r'aria-label="Verified"'
            ]
        )

        for pattern in [
            r'"followersCount":\s*(\d+)',
            r'(\d[\d,]+)\s+people\s+follow\s+this',
            r'([\d,]+)\s+followers'
        ]:
            if match := re.search(pattern, html, re.IGNORECASE):
                try:
                    profile['followers'] = int(match.group(1).replace(',', ''))
                    break
                except (ValueError, AttributeError):
                    continue

        reactions = len(re.findall(
            r'aria-label="[^"]*(Like|Love|Wow|Haha|Sad|Angry)[^"]*"',
            html,
            re.IGNORECASE
        ))
        comments = len(re.findall(r'comment(s?)', html, re.IGNORECASE))
        profile['engagement_rate'] = min(
            safe_divide(reactions + comments, profile['posts_count'] * 3),
            0.9
        )

        profile['has_profile_photo'] = bool(re.search(
            r'profile_pic|profile.*picture',
            html,
            re.IGNORECASE
        ))
        profile['has_cover_photo'] = bool(re.search(
            r'cover_photo|cover.*image',
            html,
            re.IGNORECASE
        ))
        
        if bio_match := re.search(
            r'<div[^>]*?(about|bio)[^>]*>(.*?)</div>',
            html,
            re.IGNORECASE | re.DOTALL
        ):
            clean_text = re.sub('<[^>]+>', '', bio_match.group(2))
            profile['bio_length'] = len(clean_text.strip())

        return profile
    except Exception as e:
        logger.error(f"Parsing error for {username}: {str(e)}")
        return profile

def calculate_scores(profile_data: Dict) -> Dict:
    followers = max(1, profile_data['followers'])
    posts = max(1, profile_data['posts_count'])
    
    scores = {
        'verification': 100 if profile_data['is_verified'] else 0,
        'followers': min(100, math.log10(followers) * 20 + (10 if profile_data['is_verified'] else 0)),
        'engagement': min(100, profile_data['engagement_rate'] * 100),
        'completeness': (40 if profile_data['has_profile_photo'] else 0) +
                       (30 if profile_data['has_cover_photo'] else 0) +
                       min(30, profile_data['bio_length'] / 10),
        'activity': min(100, math.log10(posts) * 25 + (10 if profile_data['is_verified'] else 0))
    }
    
    weighted_total = sum(scores[k] * WEIGHTS[k] for k in scores)
    
    return {
        'raw_scores': scores,
        'weighted_scores': {k: v * WEIGHTS[k] for k, v in scores.items()},
        'total_score': weighted_total,
        'tier': determine_tier(followers, profile_data['is_verified'])
    }

@app.post("/facebook-score", response_model=CentralScoreResponse)
async def central_score(request: CentralScoreRequest, background_tasks: BackgroundTasks = None):
    facebook_requests = []
    
    for score_request in request.requests:
        if score_request.type == "SOCIAL_SCORE":
            for social_req in score_request.data:
                if social_req.social_media == "facebook":
                    facebook_requests.append(social_req)
    
    if not facebook_requests:
        raise HTTPException(400, detail="No Facebook score requests found")
    
    facebook_scores = []
    for fb_req in facebook_requests:
        html = await fetch_profile(fb_req.username)
        if not html:
            continue
            
        profile_data = parse_facebook_html(html, fb_req.username)
        score_results = calculate_scores(profile_data)
        final_score = scale_to_range(score_results['total_score'])
        
        breakdown = {
            "profile_score": {
                "value": score_results['raw_scores']['verification'] + score_results['raw_scores']['completeness'],
                "max": 200.0
            },
            "network_score": {
                "value": score_results['raw_scores']['followers'],
                "max": 100.0
            },
            "activity_score": {
                "value": score_results['raw_scores']['engagement'] + score_results['raw_scores']['activity'],
                "max": 200.0
            }
        }
        
        facebook_scores.append({
            "score": final_score,
            "breakdown": breakdown
        })
    
    if not facebook_scores:
        raise HTTPException(404, detail="No valid Facebook profiles processed")
    
    avg_score = sum(s['score'] for s in facebook_scores) / len(facebook_scores)
    combined_breakdown = {
        "profile_score": {
            "value": sum(s['breakdown']['profile_score']['value'] for s in facebook_scores) / len(facebook_scores),
            "max": 200.0
        },
        "network_score": {
            "value": sum(s['breakdown']['network_score']['value'] for s in facebook_scores) / len(facebook_scores),
            "max": 100.0
        },
        "activity_score": {
            "value": sum(s['breakdown']['activity_score']['value'] for s in facebook_scores) / len(facebook_scores),
            "max": 200.0
        }
    }
    
    response = CentralScoreResponse(
        fayda_number=request.fayda_number,
        combined_scores={
            "SOCIAL_SCORE": SocialScoreResponse(
                fayda_number=request.fayda_number,
                score=round(avg_score),
                risk_level=get_risk_level(avg_score),
                score_breakdown=combined_breakdown,
                timestamp=datetime.now().isoformat()
            )
        }
    )

    if request.callbackUrl:
        background_tasks.add_task(send_callback, str(request.callbackUrl), response.dict())

    return response

async def send_callback(url: str, data: dict):
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=data, timeout=CALLBACK_TIMEOUT)
                response.raise_for_status()
                return
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(1 * (attempt + 1))

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.on_event("startup")
async def verify_credentials():
    if not all(os.getenv(var) for var in ['FACEBOOK_EMAIL', 'FACEBOOK_PASSWORD']):
        raise RuntimeError("Missing Facebook credentials")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7070)