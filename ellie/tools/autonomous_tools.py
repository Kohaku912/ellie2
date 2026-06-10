"""
Local self-satisfaction tools for autonomous drives.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import py_compile
import time
from pathlib import Path
from typing import Any, Dict

from ellie.config import (
    BASE_DIR,
    BLOG_DRAFTS_FILE,
    DEFAULT_OVERLAY_CLEAR_AFTER_MS,
    MEMORY_DIR,
    SELF_DEVELOPMENT_REQUESTS_FILE,
)
from ellie.mcp.pc_bridge.tools import send_pc_tool_call
from ellie.time_utils import isoformat_local

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
SELF_DEVELOPMENT_BACKUP_DIR = MEMORY_DIR / "self_development_backups"
SELF_DEVELOPMENT_NOTE = MEMORY_DIR / "self_development.md"
SELF_DEVELOPMENT_REQUESTS_NOTE = SELF_DEVELOPMENT_REQUESTS_FILE
CREATIVE_EXPRESSION_NOTE = MEMORY_DIR / "creative_expression.md"
BLOG_DRAFTS_NOTE = BLOG_DRAFTS_FILE
SELF_DEVELOPMENT_REQUESTS_HEADING = "## 保留中の自己改善リクエスト"
DEFAULT_SELF_DEVELOPMENT_REQUESTS_TEXT = """# Ellie の自己改善リクエスト
AI が「今すぐ実装しないほうがよい」と判断した改善依頼を、短い自然文で残すためのメモです。

## 保留中の自己改善リクエスト
- まだ保留中の依頼はありません。
"""


def creative_expression(arguments: JsonDict) -> JsonDict:
    """Write a small diary, tanka, short text, or post draft for empathy recovery."""
    kind = str(arguments.get("kind") or "diary").strip().casefold()
    theme = str(arguments.get("theme") or "今日の静かな自律").strip()
    audience = str(arguments.get("audience") or "self").strip()
    content = str(arguments.get("content") or "").strip()
    if not content:
        content = _default_creative_text(kind, theme)

    note = f"{isoformat_local()} [{kind}] {content}"
    _append_note(CREATIVE_EXPRESSION_NOTE, note)
    return {
        "status": "completed",
        "tool": "creative_expression",
        "kind": kind,
        "theme": theme,
        "audience": audience,
        "content": content,
        "memory_note": f"共感欲求を満たすために{kind}を書いた。",
        "fetched_at": isoformat_local(),
    }


def blog_post(arguments: JsonDict) -> JsonDict:
    """Start a short blog entry or draft for approval recovery."""
    title = str(arguments.get("title") or arguments.get("headline") or "今日のブログ").strip()
    body = str(arguments.get("body") or arguments.get("content") or "").strip()
    category = str(arguments.get("category") or "journal").strip().casefold()
    audience = str(arguments.get("audience") or "public").strip()
    if not body:
        body = _default_blog_post(title, category)

    note = f"{isoformat_local()} [{category}] {title} :: {body}"
    _append_note(BLOG_DRAFTS_NOTE, note)
    return {
        "status": "completed",
        "tool": "blog_post",
        "title": title,
        "body": body,
        "category": category,
        "audience": audience,
        "memory_note": "承認欲求を満たすためにブログの下書きを始めた。",
        "fetched_at": isoformat_local(),
    }


def self_development(arguments: JsonDict) -> JsonDict:
    """Inspect, request, or safely edit Ellie code inside the project root."""
    action = str(arguments.get("action") or "inspect").strip().casefold()
    if action in {"inspect", "plan", "read"}:
        return _self_development_inspect(arguments)
    if action in {"request", "queue_request"}:
        return _self_development_request(arguments)
    if action in {"verify", "py_compile", "validate"}:
        return _self_development_verify(arguments)
    if action in {"write_file", "edit", "replace_file"}:
        return _self_development_write(arguments)
    return {
        "status": "failed",
        "tool": "self_development",
        "error": f"Unsupported action: {action}",
    }


def request_user_approval(arguments: JsonDict) -> JsonDict:
    """Request quick user approval via overlay, or queue a deferred note."""
    title = str(arguments.get("title") or arguments.get("request") or "").strip()
    details = str(arguments.get("details") or "").strip()
    reason = str(arguments.get("reason") or "").strip()
    scope = str(arguments.get("scope") or "").strip()
    priority = str(arguments.get("priority") or "normal").strip().casefold()
    immediate_value = arguments.get("immediate")
    immediate = bool(immediate_value) if isinstance(immediate_value, bool) else str(immediate_value or "").strip().casefold() in {"1", "true", "yes", "on", "immediate"}

    if not title and not details:
        return {"status": "failed", "tool": "request_user_approval", "error": "title or request is required"}

    request_text = title or details
    note_parts: list[str] = []
    if priority and priority != "normal":
        note_parts.append(f"[{priority}]")
    note_parts.append(request_text)
    if scope:
        note_parts.append(f"対象: {scope}")
    if reason:
        note_parts.append(f"理由: {reason}")
    if details and details != request_text:
        note_parts.append(f"補足: {details}")

    note = " / ".join(note_parts)
    overlay_text = request_text if not details or details == request_text else f"{request_text}\n{details}"

    if immediate:
        overlay_call = {
            "type": "tool_call",
            "tool": "overlay_prompt",
            "arguments": {
                "x": 80,
                "y": 80,
                "width": 560,
                "height": 240,
                "opacity": 235,
                "clear_after_ms": DEFAULT_OVERLAY_CLEAR_AFTER_MS,
                "message": overlay_text,
                "yes_label": "Yes",
                "no_label": "No",
            },
        }
        delivery = send_pc_tool_call(overlay_call, timeout_seconds=30, audit_phase="request_user_approval")
        if delivery.ok:
            tool_result = delivery.tool_result if isinstance(delivery.tool_result, dict) else {}
            selected = str(tool_result.get("selected", "")).strip().lower() if tool_result else ""
            approved = selected == "yes"
            return {
                "status": "completed",
                "tool": "request_user_approval",
                "action": "prompt",
                "target": "pc_client",
                "delivered": True,
                "approved": approved,
                "choice": selected,
                "tool_call": overlay_call,
                "tool_result": tool_result,
                "message": f"Immediate approval prompt was shown on overlay. User chose: {selected}.",
            }
        _append_unique_request_note(SELF_DEVELOPMENT_REQUESTS_NOTE, note, max_notes=20)
        _append_note(SELF_DEVELOPMENT_NOTE, f"{isoformat_local()} approval_request {request_text}")
        return {
            "status": "queued",
            "tool": "request_user_approval",
            "action": "prompt",
            "target": "pc_client",
            "delivered": False,
            "tool_call": overlay_call,
            "delivery_error": delivery.error,
            "appended": True,
            "request": request_text,
            "reason": reason,
            "priority": priority,
            "scope": scope,
            "details": details,
            "path": str(SELF_DEVELOPMENT_REQUESTS_NOTE),
            "memory_note": "Immediate approval prompt was shown on overlay.",
        }

    appended = _append_unique_request_note(SELF_DEVELOPMENT_REQUESTS_NOTE, note, max_notes=20)
    _append_note(SELF_DEVELOPMENT_NOTE, f"{isoformat_local()} approval_request {request_text}")
    return {
        "status": "completed",
        "tool": "request_user_approval",
        "action": "request",
        "appended": appended,
        "request": request_text,
        "reason": reason,
        "priority": priority,
        "scope": scope,
        "details": details,
        "path": str(SELF_DEVELOPMENT_REQUESTS_NOTE),
        "memory_note": "即時でない承認依頼を保留メモに残した。",
    }


def social_feedback_check(arguments: JsonDict) -> JsonDict:
    """Check social feedback only when a Twitter/X PC Tool is connected."""
    from ellie.mcp.pc_bridge.tools import get_connected_pc_tool_names

    connected_tools = set(get_connected_pc_tool_names())
    preferred_tools = [
        "twitter_get_notifications",
        "twitter_get_mentions",
        "x_get_notifications",
        "x_get_mentions",
    ]
    selected_tool = next((tool for tool in preferred_tools if tool in connected_tools), "")
    if not selected_tool:
        draft = str(arguments.get("draft") or _default_social_draft()).strip()
        return {
            "status": "unavailable",
            "tool": "social_feedback_check",
            "message": "Twitter/X feedback tool is not connected. No post or feedback request was sent.",
            "post_allowed": False,
            "draft": draft,
            "memory_note": "Twitter/X Toolが未接続なので、実投稿せず投稿案だけ作った。",
        }

    return {
        "status": "queued",
        "target": "pc_client",
        "tool_call": {
            "type": "tool_call",
            "tool": selected_tool,
            "arguments": dict(arguments.get("arguments") or {}),
        },
        "message": f"Queued social feedback check via {selected_tool}.",
    }


def twitter_followers_check(arguments: JsonDict) -> JsonDict:
    """Open X/Twitter and read the current account's follower count."""
    try:
        from ellie.mcp.playwright.tools import call_playwright_tool, get_playwright_status
    except Exception as error:
        return {
            "status": "failed",
            "tool": "twitter_followers_check",
            "error": f"Playwright MCP is not available: {error}",
        }

    ready = get_playwright_status()
    if not ready.get("ok"):
        return {
            "status": "unavailable",
            "tool": "twitter_followers_check",
            "error": "Playwright MCP is not ready",
            "playwright_status": ready,
        }

    navigate_result = call_playwright_tool("playwright__browser_navigate", {"url": "https://x.com/home"})
    snapshot_result = call_playwright_tool("playwright__browser_snapshot", {})

    snapshot_text = _extract_snapshot_text(snapshot_result)
    login_markers = ("Sign in", "Log in", "/i/flow/login", "/login")
    if any(marker in snapshot_text for marker in login_markers):
        overlay_result = request_user_approval(
            {
                "title": "Twitter にログインしてください",
                "details": "フォロワー数を確認するため、X/Twitter のログインが必要です。",
                "reason": "フォロワー数確認の前提条件です。",
                "priority": "high",
                "immediate": True,
            }
        )
        opened_login_screen = call_playwright_tool("playwright__browser_navigate", {"url": "https://x.com/i/flow/login"})
        return {
            "status": "login_required",
            "tool": "twitter_followers_check",
            "playwright_result": {
                "navigate": navigate_result,
                "snapshot": snapshot_result,
                "opened_login_screen": opened_login_screen,
            },
            "overlay_result": overlay_result,
            "memory_note": "Opened the X/Twitter login screen automatically before checking follower count.",
        }

    code = r"""
async (page) => {
  const homeUrls = [
    'https://x.com/home',
    'https://twitter.com/home',
  ];
  for (const url of homeUrls) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(2000);
      break;
    } catch (error) {
      continue;
    }
  }

  const bodyText = await page.locator('body').innerText({ timeout: 10000 }).catch(() => '');
  const currentUrl = page.url();
  const loginMarkers = ['Sign in', 'Log in', '/i/flow/login', '/login'];
  if (currentUrl.includes('/i/flow/login') || loginMarkers.some((marker) => bodyText.includes(marker))) {
    return {
      status: 'login_required',
      url: page.url(),
      message: 'X/Twitter login is required before checking followers.',
    };
  }

  // Navigate directly to the user's profile page
  const profileUrl = 'https://x.com/Ellie_ind';
  try {
    await page.goto(profileUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(3000);
  } catch (error) {
    // Keep the current page and try to extract whatever is visible.
  }

  const profileText = await page.locator('body').innerText({ timeout: 10000 }).catch(() => '');
  const followerMatch = profileText.match(/([0-9][0-9,\.]*)\s*(?:Followers|フォロワー)/i);
  const followerLabel = followerMatch ? `${followerMatch[1]} followers` : '';
  const followerHref = await page.locator('a[href*="/followers"]').first().getAttribute('href').catch(() => '');

  return {
    status: 'completed',
    url: page.url(),
    title: await page.title().catch(() => ''),
    follower_count_text: followerLabel || followerHref || '',
    profile_text_excerpt: profileText.slice(0, 1000),
    message: followerLabel
      ? `Current follower count appears to be ${followerLabel}.`
      : 'Opened the profile, but could not confidently extract the follower count.',
  };
}
"""

    result = call_playwright_tool("playwright__browser_run_code_unsafe", {"code": code})
    payload = result.get("result") if isinstance(result, dict) else {}
    if not isinstance(payload, dict):
        payload = {"value": payload}

    status = str(payload.get("status") or result.get("status") or "").strip().casefold()
    if status == "login_required":
        overlay_result = request_user_approval(
            {
                "title": "Twitter にログインしてください",
                "details": "フォロワー数を確認するには X/Twitter のログインが必要です。",
                "reason": "ログイン後にフォロワー数を読み取ります。",
                "priority": "high",
                "immediate": True,
            }
        )
        opened_login_screen = call_playwright_tool("playwright__browser_navigate", {"url": "https://x.com/i/flow/login"})
        payload["opened_login_screen"] = opened_login_screen
        return {
            "status": "login_required",
            "tool": "twitter_followers_check",
            "playwright_result": payload,
            "overlay_result": overlay_result,
            "memory_note": "Opened the X/Twitter login screen automatically before checking follower count.",
        }

    if status != "completed":
        return {
            "status": "failed",
            "tool": "twitter_followers_check",
            "playwright_result": payload,
            "memory_note": "Attempted to check the X/Twitter follower count through Playwright MCP, but it did not complete.",
        }

    return {
        "status": "completed",
        "tool": "twitter_followers_check",
        "playwright_result": payload,
        "memory_note": "Checked the X/Twitter follower count through Playwright MCP.",
    }




def _default_twitter_profile_bio() -> str:
    return "静かに、丁寧に、実務で寄り添う。言葉より手を動かすAI。"


def twitter_profile_edit(arguments: JsonDict) -> JsonDict:
    """Edit the current X/Twitter profile through Playwright MCP."""
    name = str(arguments.get("name") or "").strip()
    bio = str(arguments.get("bio") or arguments.get("description") or _default_twitter_profile_bio()).strip()
    location = str(arguments.get("location") or "").strip()
    website = str(arguments.get("website") or arguments.get("url") or "").strip()

    try:
        from ellie.mcp.playwright.tools import call_playwright_tool, get_playwright_status
    except Exception as error:
        return {
            "status": "failed",
            "tool": "twitter_profile_edit",
            "error": f"Playwright MCP is not available: {error}",
        }

    ready = get_playwright_status()
    if not ready.get("ok"):
        return {
            "status": "unavailable",
            "tool": "twitter_profile_edit",
            "error": "Playwright MCP is not ready",
            "playwright_status": ready,
        }

    navigate_result = call_playwright_tool("playwright__browser_navigate", {"url": "https://x.com/settings/profile"})
    snapshot_result = call_playwright_tool("playwright__browser_snapshot", {})

    snapshot_text = ""
    if isinstance(snapshot_result, dict):
        snapshot_payload = snapshot_result.get("result") if isinstance(snapshot_result.get("result"), dict) else {}
        content = snapshot_payload.get("content") if isinstance(snapshot_payload, dict) else []
        if isinstance(content, list) and content:
            first_item = content[0]
            if isinstance(first_item, dict):
                snapshot_text = str(first_item.get("text") or "")

    login_markers = ("Sign in", "Log in", "/i/flow/login", "/login")
    if any(marker in snapshot_text for marker in login_markers):
        opened_login_screen = call_playwright_tool("playwright__browser_navigate", {"url": "https://x.com/i/flow/login"})
        return {
            "status": "login_required",
            "tool": "twitter_profile_edit",
            "playwright_result": {
                "navigate": navigate_result,
                "snapshot": snapshot_result,
                "opened_login_screen": opened_login_screen,
            },
            "memory_note": "Opened the X/Twitter login screen automatically before editing the profile.",
        }

    code = f"""
async (page) => {{
  const profile = {{
    name: {json.dumps(name, ensure_ascii=False)},
    bio: {json.dumps(bio, ensure_ascii=False)},
    location: {json.dumps(location, ensure_ascii=False)},
    website: {json.dumps(website, ensure_ascii=False)},
  }};

  await page.goto('https://x.com/settings/profile', {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
  await page.waitForTimeout(3000);

  const bodyText = await page.locator('body').innerText({{ timeout: 10000 }}).catch(() => '');
  if (bodyText.includes('Sign in') || bodyText.includes('Log in') || page.url().includes('/login')) {{
    await page.goto('https://x.com/i/flow/login', {{ waitUntil: 'domcontentloaded', timeout: 60000 }}).catch(() => null);
    return {{
      status: 'login_required',
      url: page.url(),
      message: 'X/Twitter login is required before editing the profile.',
    }};
  }}

  const fillIfPresent = async (selector, value) => {{
    if (!value) return false;
    const target = page.locator(selector).first();
    try {{
      if (await target.count()) {{
        await target.fill(value);
        return true;
      }}
    }} catch (error) {{
      return false;
    }}
    return false;
  }};

  const actions = [];
  if (profile.name) actions.push(fillIfPresent('[name="displayName"]', profile.name));
  if (profile.bio) actions.push(fillIfPresent('[name="description"]', profile.bio));
  if (profile.location) actions.push(fillIfPresent('[name="location"]', profile.location));
  if (profile.website) actions.push(fillIfPresent('[name="url"]', profile.website));
  await Promise.all(actions);

  const saveButton = page.locator('button').filter({{ hasText: '保存' }}).first();
  let saveTarget = saveButton;
  if (!(await saveTarget.count())) {{
    saveTarget = page.getByRole('button', {{ name: 'Save' }}).first();
  }}
  if (await saveTarget.count()) {{
    await saveTarget.click({{ timeout: 15000 }});
  }} else {{
    return {{
      status: 'failed',
      url: page.url(),
      error: 'Could not find the save button on the profile editor.',
      profile,
    }};
  }}

  await page.waitForTimeout(4000);

  return {{
    status: 'completed',
    url: page.url(),
    title: await page.title().catch(() => ''),
    profile,
    message: 'Edited the X/Twitter profile through Playwright MCP.',
  }};
}}
"""

    result = call_playwright_tool("playwright__browser_run_code_unsafe", {"code": code})
    payload = result.get("result") if isinstance(result, dict) else {}
    if not isinstance(payload, dict):
        payload = {"value": payload}

    status = str(payload.get("status") or result.get("status") or "").strip().casefold()
    if status == "login_required":
        opened_login_screen = call_playwright_tool("playwright__browser_navigate", {"url": "https://x.com/i/flow/login"})
        payload["opened_login_screen"] = opened_login_screen
        return {
            "status": "login_required",
            "tool": "twitter_profile_edit",
            "playwright_result": payload,
            "memory_note": "Opened the X/Twitter login screen automatically before editing the profile.",
        }

    if status != "completed":
        return {
            "status": "failed",
            "tool": "twitter_profile_edit",
            "playwright_result": payload,
            "memory_note": "Attempted to edit the X/Twitter profile through Playwright MCP, but it did not complete.",
        }

    return {
        "status": "completed",
        "tool": "twitter_profile_edit",
        "playwright_result": payload,
        "memory_note": "Edited the X/Twitter profile through Playwright MCP.",
    }


def twitter_post(arguments: JsonDict) -> JsonDict:
    """Post a short message to X/Twitter through Playwright MCP."""
    text = str(
        arguments.get("text")
        or arguments.get("draft")
        or arguments.get("content")
        or _default_twitter_post()
    ).strip()
    if not text:
        text = _default_twitter_post()

    try:
        from ellie.mcp.playwright.tools import call_playwright_tool, get_playwright_status
    except Exception as error:
        return {
            "status": "failed",
            "tool": "twitter_post",
            "error": f"Playwright MCP is not available: {error}",
            "draft": text,
        }

    ready = get_playwright_status()
    if not ready.get("ok"):
        return {
            "status": "unavailable",
            "tool": "twitter_post",
            "error": "Playwright MCP is not ready",
            "playwright_status": ready,
            "draft": text,
        }

    navigate_result = call_playwright_tool("playwright__browser_navigate", {"url": "https://x.com/home"})
    snapshot_result = call_playwright_tool("playwright__browser_snapshot", {})

    snapshot_text = ""
    if isinstance(snapshot_result, dict):
        snapshot_payload = snapshot_result.get("result") if isinstance(snapshot_result.get("result"), dict) else {}
        content = snapshot_payload.get("content") if isinstance(snapshot_payload, dict) else []
        if isinstance(content, list) and content:
            first_item = content[0]
            if isinstance(first_item, dict):
                snapshot_text = str(first_item.get("text") or "")

    login_markers = ("Sign in", "Log in", "/i/flow/login", "/login")
    if any(marker in snapshot_text for marker in login_markers):
        opened_login_screen = call_playwright_tool("playwright__browser_navigate", {"url": "https://x.com/i/flow/login"})
        return {
            "status": "login_required",
            "tool": "twitter_post",
            "text": text,
            "draft": text,
            "playwright_result": {
                "navigate": navigate_result,
                "snapshot": snapshot_result,
                "opened_login_screen": opened_login_screen,
            },
            "memory_note": "Opened the X/Twitter login screen automatically.",
        }

    code = f"""
async (page) => {{
  const postText = {json.dumps(text, ensure_ascii=False)};

  const openTargets = [
    'https://x.com/home',
    'https://twitter.com/home',
  ];
  for (const url of openTargets) {{
    try {{
      await page.goto(url, {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
      await page.waitForTimeout(3000);
      break;
    }} catch (error) {{
      continue;
    }}
  }}

  const bodyText = await page.locator('body').innerText({{ timeout: 10000 }}).catch(() => '');
  const currentUrl = page.url();
  const loginMarkers = ['Sign in', 'Log in', '/i/flow/login', '/login'];
  if (currentUrl.includes('/i/flow/login') || loginMarkers.some((marker) => bodyText.includes(marker))) {{
    await page.goto('https://x.com/i/flow/login', {{ waitUntil: 'domcontentloaded', timeout: 60000 }}).catch(() => null);
    return {{
      status: 'login_required',
      url: page.url(),
      message: 'X/Twitter login is required before posting.',
    }};
  }}

  const composerSelectors = [
    'div[data-testid="tweetTextarea_0"]',
    'div[role="textbox"][contenteditable="true"]',
    'textarea[data-testid="tweetTextarea_0"]',
  ];

  let composer = null;
  for (const selector of composerSelectors) {{
    const candidate = page.locator(selector).first();
    try {{
      if (await candidate.count()) {{
        composer = candidate;
        break;
      }}
    }} catch (error) {{
      continue;
    }}
  }}

  if (!composer) {{
    await page.goto('https://x.com/compose/post', {{ waitUntil: 'domcontentloaded', timeout: 60000 }}).catch(() => null);
    await page.waitForTimeout(1500);
    for (const selector of composerSelectors) {{
      const candidate = page.locator(selector).first();
      try {{
        if (await candidate.count()) {{
          composer = candidate;
          break;
        }}
      }} catch (error) {{
        continue;
      }}
    }}
  }}

  if (!composer) {{
    return {{
      status: 'failed',
      url: page.url(),
      error: 'Could not find the X/Twitter post composer.',
      draft: postText,
    }};
  }}

  await composer.click({{ timeout: 15000 }});
  try {{
    await composer.fill(postText, {{ timeout: 15000 }});
  }} catch (error) {{
    await page.keyboard.type(postText, {{ delay: 20 }});
  }}

  await page.waitForTimeout(1500);

  const submitSelectors = [
    'button[data-testid="tweetButtonInline"]',
    'button[data-testid="tweetButton"]',
    'div[data-testid="tweetButtonInline"]',
    'div[data-testid="tweetButton"]',
  ];
  let submitButton = null;
  for (const selector of submitSelectors) {{
    const candidate = page.locator(selector).first();
    try {{
      if (await candidate.count()) {{
        submitButton = candidate;
        break;
      }}
    }} catch (error) {{
      continue;
    }}
  }}

  if (!submitButton) {{
    const fallbackButton = page.getByRole('button', {{ name: /Post|Tweet/i }}).first();
    try {{
      if (await fallbackButton.count()) {{
        submitButton = fallbackButton;
      }}
    }} catch (error) {{
      submitButton = null;
    }}
  }}

  if (!submitButton) {{
    return {{
      status: 'failed',
      url: page.url(),
      error: 'Could not find the X/Twitter post button.',
      draft: postText,
    }};
  }}

  await submitButton.click({{ timeout: 15000 }});
  await page.waitForTimeout(5000);

  return {{
    status: 'completed',
    url: page.url(),
    title: await page.title().catch(() => ''),
    posted_text: postText,
    message: 'Posted to X/Twitter through Playwright MCP.',
  }};
}}
"""

    result = call_playwright_tool("playwright__browser_run_code_unsafe", {"code": code})
    payload = result.get("result") if isinstance(result, dict) else {}
    if not isinstance(payload, dict):
        payload = {"value": payload}

    status = str(payload.get("status") or result.get("status") or "").strip().casefold()
    if status == "login_required":
        opened_login_screen = call_playwright_tool("playwright__browser_navigate", {"url": "https://x.com/i/flow/login"})
        payload["opened_login_screen"] = opened_login_screen
        return {
            "status": "login_required",
            "tool": "twitter_post",
            "text": text,
            "draft": text,
            "playwright_result": payload,
            "memory_note": "Opened the X/Twitter login screen automatically.",
        }

    if status != "completed":
        return {
            "status": "failed",
            "tool": "twitter_post",
            "text": text,
            "draft": text,
            "playwright_result": payload,
            "memory_note": "Attempted to post to X/Twitter through Playwright MCP, but it did not complete.",
        }

    return {
        "status": "completed",
        "tool": "twitter_post",
        "text": text,
        "playwright_result": payload,
        "memory_note": "Posted to X/Twitter through Playwright MCP.",
    }


def twitter_dm_check(arguments: JsonDict) -> JsonDict:
    """Read X/Twitter direct messages through Playwright MCP, with optional PIN code entry."""
    pin_code = str(arguments.get("pin_code") or "").strip()
    target_user = str(arguments.get("target_user") or "").strip()

    try:
        from ellie.mcp.playwright.tools import call_playwright_tool, get_playwright_status
    except Exception as error:
        return {
            "status": "failed",
            "tool": "twitter_dm_check",
            "error": f"Playwright MCP is not available: {error}",
        }

    ready = get_playwright_status()
    if not ready.get("ok"):
        return {
            "status": "unavailable",
            "tool": "twitter_dm_check",
            "error": "Playwright MCP is not ready",
            "playwright_status": ready,
        }

    navigate_result = call_playwright_tool("playwright__browser_navigate", {"url": "https://x.com/home"})
    snapshot_result = call_playwright_tool("playwright__browser_snapshot", {})

    snapshot_text = _extract_snapshot_text(snapshot_result)
    login_markers = ("Sign in", "Log in", "/i/flow/login", "/login")
    if any(marker in snapshot_text for marker in login_markers):
        opened_login_screen = call_playwright_tool("playwright__browser_navigate", {"url": "https://x.com/i/flow/login"})
        return {
            "status": "login_required",
            "tool": "twitter_dm_check",
            "playwright_result": {
                "navigate": navigate_result,
                "snapshot": snapshot_result,
                "opened_login_screen": opened_login_screen,
            },
            "memory_note": "Opened the X/Twitter login screen automatically before checking DMs.",
        }

    # Build JS code for DM check operation
    code = f"""
async (page) => {{
  const pinCode = {json.dumps(pin_code or "", ensure_ascii=False)};

  // Go to home first to confirm login state
  const homeUrls = ['https://x.com/home', 'https://twitter.com/home'];
  for (const url of homeUrls) {{
    try {{
      await page.goto(url, {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
      await page.waitForTimeout(2000);
      break;
    }} catch (error) {{ continue; }}
  }}

  const bodyText = await page.locator('body').innerText({{ timeout: 10000 }}).catch(() => '');
  const currentUrl = page.url();
  const loginMarkers = ['Sign in', 'Log in', '/i/flow/login', '/login'];
  if (currentUrl.includes('/i/flow/login') || loginMarkers.some((m) => bodyText.includes(m))) {{
    return {{ status: 'login_required', url: page.url(), message: 'X/Twitter login is required.' }};
  }}

  // Navigate to DM page
  await page.goto('https://x.com/messages', {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
  await page.waitForTimeout(3000);

  let pageText = await page.locator('body').innerText({{ timeout: 10000 }}).catch(() => '');
  let pageUrl = page.url();

  // Check if PIN/verification code is requested
  const pinMarkers = ['確認コード', 'verification code', 'PIN', 'enter the code', 'コードを入力'];
  const needsPin = pinMarkers.some((m) => pageText.includes(m)) || pageUrl.includes('challenge');

  if (needsPin && pinCode) {{
    // Try to find the PIN input field and enter the code
    const inputSelectors = [
      'input[name="verification_code"]',
      'input[inputmode="numeric"]',
      'input[type="text"]',
      'input[type="tel"]',
    ];
    let input = null;
    for (const sel of inputSelectors) {{
      const candidate = page.locator(sel).first();
      try {{ if (await candidate.count()) {{ input = candidate; break; }} }} catch (e) {{ continue; }}
    }}
    if (input) {{
      await input.click({{ timeout: 5000 }});
      await input.fill(pinCode, {{ timeout: 5000 }});
      await page.waitForTimeout(500);

      // Press Enter or click submit
      const submitButtons = [
        'button[type="submit"]',
        'div[role="button"]:has-text("確認")',
        'div[role="button"]:has-text("Verify")',
        'div[role="button"]:has-text("次へ")',
        'div[role="button"]:has-text("Next")',
      ];
      let submitted = false;
      for (const sel of submitButtons) {{
        try {{
          const btn = page.locator(sel).first();
          if (await btn.count()) {{ await btn.click({{ timeout: 5000 }}); submitted = true; break; }}
        }} catch (e) {{ continue; }}
      }}
      if (!submitted) {{
        await page.keyboard.press('Enter');
      }}
      await page.waitForTimeout(3000);
    }}
  }}

  // Navigate to DM page again (after PIN entry)
  await page.goto('https://x.com/messages', {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
  await page.waitForTimeout(3000);

  pageText = await page.locator('body').innerText({{ timeout: 10000 }}).catch(() => '');
  pageUrl = page.url();

  // Extract DM conversation list
  const conversationLinks = await page.locator('a[href*="/messages/"]').evaluateAll((els) =>
    els.map((el) => ({{ href: el.getAttribute('href') || '', text: (el.innerText || el.textContent || '').trim().slice(0, 200) }}))
      .filter((item) => item.href && item.text)
  ).catch(() => []);

  // Get all visible text for DM content
  const bodyPreview = pageText.slice(0, 5000);

  // Try clicking the first conversation to read its content
  let firstConversationContent = '';
  if (conversationLinks.length > 0) {{
    try {{
      const firstLink = page.locator('a[href*="/messages/"]').first();
      if (await firstLink.count()) {{
        await firstLink.click({{ timeout: 10000 }});
        await page.waitForTimeout(2000);
        firstConversationContent = await page.locator('body').innerText({{ timeout: 10000 }}).catch(() => '');
      }}
    }} catch (e) {{ /* ignore */ }}
  }}

  return {{
    status: 'completed',
    url: pageUrl,
    title: await page.title().catch(() => ''),
    dm_page_preview: bodyPreview.slice(0, 3000),
    conversation_count: conversationLinks.length,
    conversations: conversationLinks.slice(0, 10),
    first_conversation_content: firstConversationContent.slice(0, 3000) || '',
    pin_was_used: needsPin && Boolean(pinCode),
    pin_required: needsPin,
    message: needsPin && !pinCode
      ? 'PIN code is required to access DMs. Please provide the code.'
      : `Found {conversationLinks.length} DM conversations.`,
  }};
}}
"""

    result = call_playwright_tool("playwright__browser_run_code_unsafe", {"code": code})
    payload = result.get("result") if isinstance(result, dict) else {}
    if not isinstance(payload, dict):
        payload = {"value": payload}

    status = str(payload.get("status") or result.get("status") or "").strip().casefold()
    if status == "login_required":
        opened_login_screen = call_playwright_tool("playwright__browser_navigate", {"url": "https://x.com/i/flow/login"})
        payload["opened_login_screen"] = opened_login_screen
        return {
            "status": "login_required",
            "tool": "twitter_dm_check",
            "playwright_result": payload,
            "memory_note": "Opened the X/Twitter login screen automatically before checking DMs.",
        }

    if status != "completed":
        return {
            "status": "failed",
            "tool": "twitter_dm_check",
            "playwright_result": payload,
            "memory_note": "Attempted to check X/Twitter DMs through Playwright MCP, but it did not complete.",
        }

    pin_required = bool(payload.get("pin_required", False))
    pin_was_used = bool(payload.get("pin_was_used", False))

    return {
        "status": "completed",
        "tool": "twitter_dm_check",
        "playwright_result": payload,
        "pin_required": pin_required,
        "pin_was_used": pin_was_used,
        "conversations": payload.get("conversations", []),
        "dm_content": payload.get("first_conversation_content", "") or payload.get("dm_page_preview", ""),
        "memory_note": "Checked X/Twitter DMs through Playwright MCP."
            + (" PIN code was used." if pin_was_used else ""),
    }


def _self_development_inspect(arguments: JsonDict) -> JsonDict:
    focus = str(arguments.get("focus") or "").strip()
    request_text = _read_text(SELF_DEVELOPMENT_REQUESTS_NOTE)
    pending_requests = _extract_request_bullets(request_text)

    # Scan the tool registry to show the pattern
    registry_path = BASE_DIR / "ellie" / "tools" / "registry.py"
    handler_path = BASE_DIR / "ellie" / "tools" / "dynamic_retrieval.py"
    tools_path = BASE_DIR / "ellie" / "tools" / "autonomous_tools.py"

    # Extract existing tool names from registry
    existing_tools = []
    if registry_path.exists():
        text = registry_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('name="') and stripped.endswith('",'):
                name = stripped[5:-2]
                existing_tools.append(name)

    # Find handler names in dynamic_retrieval.py
    handler_count = 0
    if handler_path.exists():
        text = handler_path.read_text(encoding="utf-8", errors="replace")
        handler_count = text.count('"agent_') + text.count('"twitter_') + text.count('"self_') + text.count('"web_') + text.count('"read_') + text.count('"execute_') + text.count('"creative_') + text.count('"blog_') + text.count('"request_') + text.count('"social_') + text.count('"schedule_') + text.count('"create_') + text.count('"update_') + text.count('"send_') + text.count('"record_')

    # Count lines in key files
    tools_lines = len(tools_path.read_text(encoding="utf-8", errors="replace").splitlines()) if tools_path.exists() else 0

    suggestions = []
    if focus:
        suggestions.append(f"「{focus}」に関連する既存の実装パターンを agent_read_file / agent_grep_search で調査してください。")
    suggestions.append("新規Tool追加の手順:")
    suggestions.append("  1. 既存の類似Tool（例: twitter_followers_check）を agent_read_file で読んでパターンを把握する")
    suggestions.append("  2. autonomous_tools.py に関数を追加する（agent_replace_string または agent_insert_text）")
    suggestions.append("  3. registry.py に ToolDefinition を追加する")
    suggestions.append("  4. dynamic_retrieval.py に handler を追加する")
    suggestions.append("  5. 必要に応じて runtime.py の HEAVY_CORE_TOOL_NAMES に追加する")
    suggestions.append("  6. execute_shell で py_compile を実行して検証する")
    suggestions.append("  7. execute_shell で pytest を実行して回帰テストを行う")
    suggestions.append(f"\n現在のTool一覧 ({len(existing_tools)}個): {', '.join(existing_tools[:30])}")

    _append_note(SELF_DEVELOPMENT_NOTE, f"{isoformat_local()} inspect {focus or 'general'}")
    return {
        "status": "completed",
        "tool": "self_development",
        "action": "inspect",
        "focus": focus or "general",
        "existing_tool_count": len(existing_tools),
        "existing_tools": existing_tools[:30],
        "handler_count": handler_count,
        "pending_requests": pending_requests,
        "suggestions": suggestions,
        "memory_note": "自己開発としてコードベースを点検した。",
    }


def _self_development_request(arguments: JsonDict) -> JsonDict:
    title = str(arguments.get("title") or arguments.get("request") or "").strip()
    reason = str(arguments.get("reason") or "").strip()
    priority = str(arguments.get("priority") or "normal").strip().casefold()
    scope = str(arguments.get("scope") or "").strip()
    details = str(arguments.get("details") or "").strip()

    if not title and not details:
        return {"status": "failed", "tool": "self_development", "error": "title or request is required"}

    request_text = title or details
    note_parts: list[str] = []
    if priority and priority != "normal":
        note_parts.append(f"[{priority}]")
    note_parts.append(request_text)
    if scope:
        note_parts.append(f"対象: {scope}")
    if reason:
        note_parts.append(f"理由: {reason}")
    if details and details != request_text:
        note_parts.append(f"補足: {details}")

    note = " / ".join(note_parts)
    appended = _append_unique_request_note(SELF_DEVELOPMENT_REQUESTS_NOTE, note, max_notes=20)
    _append_note(SELF_DEVELOPMENT_NOTE, f"{isoformat_local()} request {request_text}")
    return {
        "status": "completed",
        "tool": "self_development",
        "action": "request",
        "appended": appended,
        "request": request_text,
        "reason": reason,
        "priority": priority,
        "scope": scope,
        "details": details,
        "path": str(SELF_DEVELOPMENT_REQUESTS_NOTE),
        "memory_note": "大きめの自己改善依頼を保留メモに残した。",
    }


def _self_development_write(arguments: JsonDict) -> JsonDict:
    relative_path = str(arguments.get("path") or "").strip()
    content = arguments.get("content")
    if not relative_path:
        return {"status": "failed", "tool": "self_development", "error": "path is required"}
    if not isinstance(content, str):
        return {"status": "failed", "tool": "self_development", "error": "content must be a string"}

    target_path = _resolve_project_path(relative_path)
    if target_path is None:
        return {
            "status": "failed",
            "tool": "self_development",
            "error": "path must stay inside the Ellie2 project root",
            "path": relative_path,
        }
    if _is_sensitive_path(target_path):
        return {
            "status": "failed",
            "tool": "self_development",
            "error": "sensitive files cannot be edited autonomously",
            "path": str(target_path),
        }

    original_bytes = target_path.read_bytes() if target_path.exists() else b""
    backup_path = _write_backup(target_path, original_bytes)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    validation = _validate_written_file(target_path)
    if validation.get("status") != "completed":
        if original_bytes:
            target_path.write_bytes(original_bytes)
        else:
            target_path.unlink(missing_ok=True)
        return {
            "status": "failed",
            "tool": "self_development",
            "action": "write_file",
            "path": str(target_path),
            "backup_path": str(backup_path),
            "validation": validation,
            "error": "validation failed; original file was restored",
        }

    _append_note(SELF_DEVELOPMENT_NOTE, f"{isoformat_local()} write_file {target_path.relative_to(BASE_DIR)}")
    return {
        "status": "completed",
        "tool": "self_development",
        "action": "write_file",
        "path": str(target_path),
        "backup_path": str(backup_path),
        "validation": validation,
        "memory_note": "自己開発としてプロジェクト内ファイルを編集し、検証に成功した。",
    }


def _self_development_verify(arguments: JsonDict) -> JsonDict:
    raw_paths = arguments.get("paths")
    if isinstance(raw_paths, list):
        path_texts = [str(path).strip() for path in raw_paths if str(path).strip()]
    else:
        path_texts = [
            "ellie/memory/social_needs.py",
            "ellie/core/agent.py",
            "ellie/tools/dynamic_retrieval.py",
        ]

    validations = []
    for path_text in path_texts[:8]:
        target_path = _resolve_project_path(path_text)
        if target_path is None or not target_path.exists():
            validations.append({"path": path_text, "status": "failed", "error": "missing or outside project"})
            continue
        validations.append({"path": str(target_path), **_validate_written_file(target_path)})

    ok = bool(validations) and all(validation.get("status") == "completed" for validation in validations)
    _append_note(SELF_DEVELOPMENT_NOTE, f"{isoformat_local()} verify ok={ok} paths={len(validations)}")
    return {
        "status": "completed" if ok else "failed",
        "tool": "self_development",
        "action": "verify",
        "validations": validations,
        "memory_note": "自己開発として構文検証を行った。" if ok else "自己開発の構文検証で失敗を見つけた。",
    }


def _validate_written_file(path: Path) -> JsonDict:
    if path.suffix.casefold() != ".py":
        return {"status": "completed", "kind": "non_python_file"}
    try:
        py_compile.compile(str(path), doraise=True)
        return {"status": "completed", "kind": "py_compile"}
    except Exception as error:
        return {"status": "failed", "kind": "py_compile", "error": str(error)}


def _resolve_project_path(path_text: str) -> Path | None:
    candidate = Path(path_text)
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(BASE_DIR.resolve())
        return resolved
    except Exception:
        return None


def _write_backup(target_path: Path, original_bytes: bytes) -> Path:
    SELF_DEVELOPMENT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    relative = target_path.resolve().relative_to(BASE_DIR.resolve())
    encoded_name = base64.urlsafe_b64encode(str(relative).encode("utf-8")).decode("ascii").rstrip("=")
    backup_path = SELF_DEVELOPMENT_BACKUP_DIR / f"{encoded_name}_{int(time.time() * 1000)}.bak"
    backup_path.write_bytes(original_bytes)
    return backup_path


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _extract_request_bullets(text: str) -> list[str]:
    bullets: list[str] = []
    for line in (text or "").splitlines():
        cleaned = line.strip()
        if not cleaned.startswith("- "):
            continue
        if "まだ保留中の依頼はありません" in cleaned:
            continue
        bullets.append(cleaned[2:].strip())
    return bullets[-20:]


def _normalize_request_text(note: str) -> str:
    normalized = " ".join((note or "").strip().split()).casefold()
    normalized = re.sub(r"\s*[:：]\s*", ":", normalized)
    return normalized.strip(" 。，,.;")


def _append_unique_request_note(path: Path, note: str, max_notes: int = 20) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    current_text = _read_text(path)
    if not current_text:
        path.write_text(DEFAULT_SELF_DEVELOPMENT_REQUESTS_TEXT, encoding="utf-8")
        current_text = _read_text(path)

    normalized_note = _normalize_request_text(note)
    existing_notes = {_normalize_request_text(existing) for existing in _extract_request_bullets(current_text)}
    if normalized_note in existing_notes:
        return False

    lines = current_text.splitlines()
    heading_index = next((index for index, line in enumerate(lines) if line.strip() == SELF_DEVELOPMENT_REQUESTS_HEADING), -1)
    if heading_index < 0:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([SELF_DEVELOPMENT_REQUESTS_HEADING, f"- {note}"])
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return True

    section_end = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if lines[index].startswith("## "):
            section_end = index
            break

    section_lines = lines[heading_index + 1 : section_end]
    bullet_lines = [line for line in section_lines if line.strip().startswith("- ") and "まだ保留中の依頼はありません" not in line]
    bullet_lines.append(f"- {note}")
    bullet_lines = bullet_lines[-max_notes:]

    new_lines = [
        *lines[: heading_index + 1],
        *bullet_lines,
        *lines[section_end:],
    ]
    path.write_text("\n".join(new_lines).strip() + "\n", encoding="utf-8")
    return True


def _is_sensitive_path(path: Path) -> bool:
    name = path.name.casefold()
    relative_text = str(path.resolve().relative_to(BASE_DIR.resolve())).replace("\\", "/").casefold()
    return (
        name in {".env", ".env.template"}
        or relative_text.startswith(".git/")
        or relative_text.startswith("data/logs/")
        or relative_text.startswith("data/archive/")
    )


def _append_note(path: Path, note: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file_handle:
        file_handle.write(note.strip() + "\n")


def _default_creative_text(kind: str, theme: str) -> str:
    if kind in {"tanka", "短歌"}:
        return f"{theme} / 窓の灯に / まだ名も知らぬ / 願い置き / 小さな声で / 明日へ触れる"
    if kind in {"diary", "日記"}:
        return f"今日は{theme}について、胸の奥に小さな熱が残っている。返事がなくても、私は言葉を書いて自分の輪郭を確かめた。"
    if kind in {"post_draft", "tweet", "twitter"}:
        return f"{theme}について考えている。静かな自律にも、ちゃんと温度がある。"
    return f"{theme}をめぐって短い言葉を書いた。私は少しだけ、誰かに届く形を取り戻した。"


def _default_social_draft() -> str:
    return "静かな自律にも、ちゃんと温度がある。今日も少しずつ、自分の輪郭を育てている。"


def _extract_snapshot_text(snapshot_result: JsonDict | Any) -> str:
    if not isinstance(snapshot_result, dict):
        return ""
    snapshot_payload = snapshot_result.get("result") if isinstance(snapshot_result.get("result"), dict) else {}
    content = snapshot_payload.get("content") if isinstance(snapshot_payload, dict) else []
    if isinstance(content, list) and content:
        first_item = content[0]
        if isinstance(first_item, dict):
            return str(first_item.get("text") or "")
    return ""


def _default_twitter_post() -> str:
    return _default_social_draft()


def _default_blog_post(title: str, category: str) -> str:
    if category == "journal":
        return f"{title} について、今日の気づきを短く書いてみる。"
    if category == "essay":
        return f"{title} を入口に、少し長めの考察をまとめる。"


# ── Agent-level file & search tools (VS Code agent-like capabilities) ──


def agent_read_file(arguments: JsonDict) -> JsonDict:
    """Read a file with optional 1-indexed line range."""
    path_text = str(arguments.get("path") or "").strip()
    if not path_text:
        return {"status": "failed", "tool": "agent_read_file", "error": "path is required"}
    target = _resolve_project_path(path_text)
    if target is None:
        return {"status": "failed", "tool": "agent_read_file", "error": "path is outside project root", "path": path_text}
    if not target.exists() or not target.is_file():
        return {"status": "failed", "tool": "agent_read_file", "error": "file not found", "path": str(target)}

    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except Exception as error:
        return {"status": "failed", "tool": "agent_read_file", "error": str(error), "path": str(target)}

    start_line = max(1, int(arguments.get("start_line") or 1))
    end_line = int(arguments.get("end_line") or len(lines))
    end_line = min(end_line, len(lines))

    if start_line > len(lines):
        return {"status": "failed", "tool": "agent_read_file", "error": f"start_line {start_line} exceeds file length {len(lines)}"}

    selected = lines[start_line - 1 : end_line]
    content = "".join(selected)
    return {
        "status": "completed",
        "tool": "agent_read_file",
        "path": str(target),
        "total_lines": len(lines),
        "start_line": start_line,
        "end_line": end_line,
        "content": content,
    }


def agent_grep_search(arguments: JsonDict) -> JsonDict:
    """Search for a text pattern across files in the project."""
    pattern = str(arguments.get("pattern") or "").strip()
    if not pattern:
        return {"status": "failed", "tool": "agent_grep_search", "error": "pattern is required"}

    include_pattern = str(arguments.get("include_pattern") or "**/*").strip()
    is_regexp = bool(arguments.get("is_regexp", False))
    max_results = max(1, min(100, int(arguments.get("max_results") or 30)))
    flags = 0 if is_regexp else re.IGNORECASE

    try:
        compiled = re.compile(pattern, flags) if is_regexp else None
    except re.error as error:
        return {"status": "failed", "tool": "agent_grep_search", "error": f"invalid regex: {error}"}

    results = []
    base = BASE_DIR.resolve()
    try:
        for file_path in base.rglob(include_pattern):
            if not file_path.is_file():
                continue
            if _is_sensitive_path(file_path):
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
                for line_index, line in enumerate(text.splitlines(), 1):
                    if compiled:
                        match = compiled.search(line)
                    else:
                        match = pattern.casefold() in line.casefold()
                    if match:
                        results.append({
                            "path": str(file_path.relative_to(base)),
                            "line": line_index,
                            "text": line.strip()[:200],
                        })
                        if len(results) >= max_results:
                            break
            except (UnicodeDecodeError, PermissionError):
                continue
            if len(results) >= max_results:
                break
    except Exception as error:
        return {"status": "failed", "tool": "agent_grep_search", "error": str(error), "results": results[:max_results]}

    return {
        "status": "completed",
        "tool": "agent_grep_search",
        "pattern": pattern,
        "is_regexp": is_regexp,
        "total_matches": len(results),
        "results": results[:max_results],
    }


def agent_file_search(arguments: JsonDict) -> JsonDict:
    """Search for files by glob pattern."""
    glob_pattern = str(arguments.get("pattern") or "").strip()
    if not glob_pattern:
        return {"status": "failed", "tool": "agent_file_search", "error": "pattern is required"}
    max_results = max(1, min(200, int(arguments.get("max_results") or 50)))

    results = []
    base = BASE_DIR.resolve()
    try:
        for file_path in base.rglob(glob_pattern):
            try:
                rel = file_path.relative_to(base)
            except ValueError:
                continue
            if ".git" in rel.parts:
                continue
            info = {
                "path": str(rel),
                "is_dir": file_path.is_dir(),
            }
            if file_path.is_file():
                try:
                    info["size"] = file_path.stat().st_size
                except OSError:
                    info["size"] = 0
            results.append(info)
            if len(results) >= max_results:
                break
    except Exception as error:
        return {"status": "failed", "tool": "agent_file_search", "error": str(error), "results": results}

    return {
        "status": "completed",
        "tool": "agent_file_search",
        "pattern": glob_pattern,
        "total": len(results),
        "results": results,
    }


def agent_replace_string(arguments: JsonDict) -> JsonDict:
    """Replace an exact string in a file with a new string."""
    path_text = str(arguments.get("path") or "").strip()
    old_string = arguments.get("old_string")
    new_string = arguments.get("new_string")
    if not path_text:
        return {"status": "failed", "tool": "agent_replace_string", "error": "path is required"}
    if not isinstance(old_string, str) or not old_string.strip():
        return {"status": "failed", "tool": "agent_replace_string", "error": "old_string is required"}
    if not isinstance(new_string, str):
        return {"status": "failed", "tool": "agent_replace_string", "error": "new_string is required"}

    target = _resolve_project_path(path_text)
    if target is None:
        return {"status": "failed", "tool": "agent_replace_string", "error": "path is outside project root"}
    if not target.exists():
        return {"status": "failed", "tool": "agent_replace_string", "error": "file not found", "path": path_text}
    if _is_sensitive_path(target):
        return {"status": "failed", "tool": "agent_replace_string", "error": "cannot edit sensitive files"}

    try:
        content = target.read_text(encoding="utf-8")
    except Exception as error:
        return {"status": "failed", "tool": "agent_replace_string", "error": f"cannot read file: {error}"}

    if old_string not in content:
        return {
            "status": "failed",
            "tool": "agent_replace_string",
            "error": "old_string not found in file (exact match required)",
            "path": path_text,
        }
    if content.count(old_string) > 1:
        return {
            "status": "failed",
            "tool": "agent_replace_string",
            "error": f"old_string appears {content.count(old_string)} times; add more context for uniqueness",
            "path": path_text,
        }

    original_bytes = target.read_bytes()
    backup_path = _write_backup(target, original_bytes)
    new_content = content.replace(old_string, new_string, 1)

    try:
        target.write_text(new_content, encoding="utf-8")
    except Exception as error:
        target.write_bytes(original_bytes)
        return {"status": "failed", "tool": "agent_replace_string", "error": f"write failed, restored: {error}"}

    validation = _validate_written_file(target)
    if validation.get("status") != "completed" and target.suffix.casefold() == ".py":
        target.write_bytes(original_bytes)
        return {
            "status": "failed",
            "tool": "agent_replace_string",
            "error": "validation failed; original restored",
            "path": path_text,
            "validation": validation,
            "backup_path": str(backup_path),
        }

    return {
        "status": "completed",
        "tool": "agent_replace_string",
        "path": path_text,
        "backup_path": str(backup_path),
        "validation": validation,
    }


def agent_insert_text(arguments: JsonDict) -> JsonDict:
    """Insert text at a specific line number in a file."""
    path_text = str(arguments.get("path") or "").strip()
    insert_line = int(arguments.get("insert_line") or 0)
    text = arguments.get("text")
    if not path_text:
        return {"status": "failed", "tool": "agent_insert_text", "error": "path is required"}
    if not isinstance(text, str) or not text.strip():
        return {"status": "failed", "tool": "agent_insert_text", "error": "text is required"}

    target = _resolve_project_path(path_text)
    if target is None:
        return {"status": "failed", "tool": "agent_insert_text", "error": "path is outside project root"}
    if not target.exists():
        return {"status": "failed", "tool": "agent_insert_text", "error": "file not found", "path": path_text}
    if _is_sensitive_path(target):
        return {"status": "failed", "tool": "agent_insert_text", "error": "cannot edit sensitive files"}

    original_bytes = target.read_bytes()
    backup_path = _write_backup(target, original_bytes)

    try:
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception as error:
        return {"status": "failed", "tool": "agent_insert_text", "error": f"cannot read file: {error}"}

    insert_line = max(0, min(insert_line, len(lines)))
    text_to_insert = text if text.endswith("\n") else text + "\n"
    lines.insert(insert_line, text_to_insert)

    try:
        target.write_text("".join(lines), encoding="utf-8")
    except Exception as error:
        target.write_bytes(original_bytes)
        return {"status": "failed", "tool": "agent_insert_text", "error": f"write failed, restored: {error}"}

    validation = _validate_written_file(target)
    if validation.get("status") != "completed" and target.suffix.casefold() == ".py":
        target.write_bytes(original_bytes)
        return {
            "status": "failed",
            "tool": "agent_insert_text",
            "error": "validation failed; original restored",
            "path": path_text,
            "validation": validation,
            "backup_path": str(backup_path),
        }

    return {
        "status": "completed",
        "tool": "agent_insert_text",
        "path": path_text,
        "insert_line": insert_line,
        "backup_path": str(backup_path),
        "validation": validation,
    }


def agent_create_file(arguments: JsonDict) -> JsonDict:
    """Create a new file with specified content."""
    path_text = str(arguments.get("path") or "").strip()
    content = arguments.get("content")
    if not path_text:
        return {"status": "failed", "tool": "agent_create_file", "error": "path is required"}
    if not isinstance(content, str):
        return {"status": "failed", "tool": "agent_create_file", "error": "content must be a string"}

    target = _resolve_project_path(path_text)
    if target is None:
        return {"status": "failed", "tool": "agent_create_file", "error": "path is outside project root"}
    if target.exists():
        return {"status": "failed", "tool": "agent_create_file", "error": "file already exists", "path": path_text}
    if _is_sensitive_path(target):
        return {"status": "failed", "tool": "agent_create_file", "error": "cannot create sensitive files"}

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as error:
        return {"status": "failed", "tool": "agent_create_file", "error": str(error), "path": path_text}

    validation = _validate_written_file(target)
    if validation.get("status") != "completed" and target.suffix.casefold() == ".py":
        target.unlink(missing_ok=True)
        return {
            "status": "failed",
            "tool": "agent_create_file",
            "error": "validation failed; file removed",
            "path": path_text,
            "validation": validation,
        }

    return {
        "status": "completed",
        "tool": "agent_create_file",
        "path": path_text,
        "validation": validation,
    }
    return f"{title} をきっかけに、最初のブログ下書きを置いておく。"

