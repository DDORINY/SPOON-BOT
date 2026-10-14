"""Shared presentation only; callers supply escaped, server-owned action markup."""
from html import escape

ICON = '<svg viewBox="0 0 64 64" aria-hidden="true" focusable="false"><path d="M12 35v-5a20 20 0 0 1 40 0v5"/><rect x="8" y="31" width="12" height="22" rx="6"/><rect x="44" y="31" width="12" height="22" rx="6"/><path d="M50 53c0 5-8 7-18 7"/></svg>'


def render_auth_page(body, *, title='SPOON 인증 관리', description='나만의 방송 도우미를 안전하게 관리하세요.', connected=False, eyebrow='ACCOUNT CONNECTION'):
    label = '계정 연결됨' if connected else '계정 미연결'
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)} · DOHA-BOT</title><link rel="stylesheet" href="/static/oauth.css"></head>
<body><div class="auth-shell"><header class="topbar"><a class="brand" href="/" aria-label="DOHA-BOT 대시보드"><span class="brand-mark" aria-hidden="true">D</span><span><strong>DOHA-BOT</strong><small>DJ STREAM ASSISTANT</small></span></a><span class="connection {'connected' if connected else ''}"><span class="status-dot" aria-hidden="true"></span>{label}</span></header>
<main id="main-content"><div class="intro"><div class="hero-icon">{ICON}</div><p class="eyebrow">{escape(eyebrow)}</p><h1>{escape(title)}</h1><p class="description">{escape(description)}</p></div>
<section class="login-card" aria-label="SPOON 계정 연결"><aside class="card-aside"><span class="edition">YOUR STREAM, YOUR SPACE</span><h2>좋은 방송의 시작,<br>가벼운 연결 하나.</h2><p>방송에 집중하세요.<br>도우미는 내 PC에서 함께합니다.</p><div class="broadcast-art" aria-hidden="true"><span></span><span></span><span></span><span></span><span></span><span></span><span></span></div><div class="local-badge">LOCAL EDITION <span>개인용 방송 도우미</span></div></aside><div class="card-content">{body}</div></section>
<p class="below-card">로그인은 SPOON 공식 페이지에서 안전하게 진행됩니다.</p></main>
<footer><span>© DOHA-BOT <span class="footer-divider">/</span> Local Edition</span><span>내 PC에서 실행되는 방송 도우미</span></footer></div></body></html>'''
