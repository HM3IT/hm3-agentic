## Overview  
This project automates downloading and uploading videos or images to popular social platforms—such as YouTube, Facebook, and TikTok—using a combination of specialized tools and intelligent agents. Users interact via a conversational interface: the agent provides necessary information and generates OAuth URLs to authenticate with Google before performing uploads. Under the hood, Litestar powers the HTTP API, while local or cloud-hosted LLMs (configured in `.env.example`) drive decision-making and prompt generation.

## Features  
- **Agent-driven workflow**: Chat with an AI agent to request downloads/uploads.  
- **Social platform support**: Built-in tools for YouTube, Facebook, TikTok (easily extendable).  
- **OAuth2 authentication**: Secure Google OAuth for YouTube uploads with endpoint-based code exchange.  
- **Flexible model backend**: Use local Ollama-hosted models or point to any LLM API via the `MODEL_URL` in `.env.example`.   

## Getting Started  

### Prerequisites  
- Docker & Docker Compose (optional but recommended)  
- Python 3.12+  
- Reddit API credentials
- Google OAuth 2.0 credentials

### Installation  
```bash
git clone git@github.com:HM3IT/hm3-agentic.git
cd hm3-agentic
cp .env.example .env
bash start.sh
```

## Configuration
Edit .env to set your preferred model:

```bash
MODEL_FAMILY=
MODEL_NAME=
MODEL_BASE_URL=http://localhost:11434 # local ollama
# or
MODEL_BASE_URL=https://example.com/v1/ # remote

API_BASE_URL="" # url that the tools will interact with e.g. retrieved information
```

## Usage

- Crawl and download content from any subreddit with a dynamic subreddit name. Available algorithms: `top` and `new`. You can also pass desired keywords to filter results.  
  *Example:* `Download the top 5 videos from <Subreddit_name>`
- TO enable subbredit data scraping or crawling, user need to setup Reddit API. Please Read [How to setup](https://www.reddit.com/r/reddit.com/wiki/api/).

- After downloading, the user can upload the videos to YouTube.

- If the user hasn’t authenticated yet, the agent will provide an OAuth URL. You’ll need to [set up OAuth](https://developers.google.com/youtube/v3/getting-started) to use the YouTube API.

## Architecture

- **Litestar** handles HTTP routing for agent tools and OAuth endpoints.  
- **Agents & Tools** live under `app/domain/chat/tools.py`.  
- **State persistence** is implemented via JSON history files (one per session ID) in `download_history/`.

## Future Plans

- **Audio generation:** Multilingual transcript translation and AI-driven voiceovers.  
- **Hybrid NLP & LLM translation:** English ↔ Burmese transcript conversion.  
- **Plugin system:** Easy integration of third-party or custom tools.  
- **Expanded platform support:** Facebook Reels, Instagram, and more.  
