"""The Linux port's strings. Every string the tray or a notification shows goes
through here; Korean falls back to English, English to the key."""

from __future__ import annotations

import locale
import os

from .core.models import AppLanguage

EN = {
    "now": "now",
    "window_not_open": "starts on first use",
    "window_not_reported": "not reported",
    "loading": "Loading…",
    "updated_at": "Updated {time}",
    "source_cache": "cached value",
    "source_statusline": "from status line",
    "refresh_now": "Refresh now",
    "display": "Display",
    "display_lowest": "Lowest remaining",
    "display_both": "Both",
    "display_claude": "Claude only",
    "display_codex": "Codex only",
    "label_style": "Provider label",
    "label_abbreviation": "Short (Cl / Cdx)",
    "label_full": "Full name",
    "refresh_interval": "Refresh interval",
    "minutes": "{n} min",
    "providers": "Providers",
    "show_countdown": "Show reset countdown",
    "notifications": "Notifications",
    "renew_claude": "Renew Claude token with Claude Code",
    "language": "Language",
    "language_system": "System",
    "launch_at_login": "Launch at login",
    "open_data_folder": "Open data folder",
    "quit": "Quit Tokki",
    "five_hour_alert_title": "5h usage is low",
    "seven_day_alert_title": "7d usage is low",
    "reset_alert_title": "Usage window reset soon",
    "reset_alert_body": "{provider} {window} resets in {time} with {percent}% unused",
    "stalled_alert_title": "Usage stopped updating",
    "stalled_alert_body": "{provider} last measured {time} ago",
    "freed_up_alert_title": "Usage available again",
    "freed_up_alert_body": "{provider} {window} has reset: {percent}% left",
    "alert_thresholds": "Alert thresholds",
    "five_hour_threshold": "5h low at",
    "seven_day_threshold": "7d low at",
    "reset_alert_minutes": "Reset warning",
    "minutes_before": "{n} min before",
    "disabled": "off",
    "pause_polling": "Pause updates",
    "pause_1h": "For 1 hour",
    "pause_3h": "For 3 hours",
    "pause_until_resumed": "Until I resume",
    "resume_now": "Resume now",
    "updates_paused": "Updates paused",
    "update_available": "New version {version}",
    "download_update": "Download",
    "check_for_updates": "Check for updates",
}

KO = {
    "now": "지금",
    "window_not_open": "첫 사용 시 시작",
    "window_not_reported": "보고 안 됨",
    "loading": "불러오는 중…",
    "updated_at": "{time} 업데이트",
    "source_cache": "캐시된 값",
    "source_statusline": "상태줄 값",
    "refresh_now": "지금 새로고침",
    "display": "표시",
    "display_lowest": "가장 적게 남은 값",
    "display_both": "둘 다",
    "display_claude": "Claude만",
    "display_codex": "Codex만",
    "label_style": "제공자 표기",
    "label_abbreviation": "약어 (Cl / Cdx)",
    "label_full": "전체 이름",
    "refresh_interval": "새로고침 간격",
    "minutes": "{n}분",
    "providers": "제공자",
    "show_countdown": "리셋까지 남은 시간 표시",
    "notifications": "알림",
    "renew_claude": "Claude Code로 Claude 토큰 갱신",
    "language": "언어",
    "language_system": "시스템",
    "launch_at_login": "로그인 시 자동 시작",
    "open_data_folder": "데이터 폴더 열기",
    "quit": "Tokki 종료",
    "five_hour_alert_title": "5시간 사용량 부족",
    "seven_day_alert_title": "7일 사용량 부족",
    "reset_alert_title": "사용량 창 리셋 임박",
    "reset_alert_body": "{provider} {window} {time} 후 리셋 — {percent}% 남음",
    "stalled_alert_title": "사용량 갱신 멈춤",
    "stalled_alert_body": "{provider} 마지막 측정 {time} 전",
    "freed_up_alert_title": "다시 사용 가능",
    "freed_up_alert_body": "{provider} {window} 리셋됨 — 잔량 {percent}%",
    "alert_thresholds": "알림 기준",
    "five_hour_threshold": "5시간 부족 기준",
    "seven_day_threshold": "7일 부족 기준",
    "reset_alert_minutes": "리셋 임박 알림",
    "minutes_before": "{n}분 전",
    "disabled": "꺼짐",
    "pause_polling": "업데이트 일시중지",
    "pause_1h": "1시간",
    "pause_3h": "3시간",
    "pause_until_resumed": "재개할 때까지",
    "resume_now": "지금 재개",
    "updates_paused": "업데이트 일시중지됨",
    "update_available": "새 버전 {version}",
    "download_update": "다운로드",
    "check_for_updates": "업데이트 확인",
}


def _system_is_korean() -> bool:
    for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(name)
        if value:
            return value.lower().startswith("ko")
    try:
        code = locale.getlocale()[0] or ""
    except ValueError:
        code = ""
    return code.lower().startswith("ko")


class Localizer:
    def __init__(self, language: AppLanguage = AppLanguage.ENGLISH):
        if language is AppLanguage.SYSTEM:
            language = AppLanguage.KOREAN if _system_is_korean() else AppLanguage.ENGLISH
        self.language = language

    def text(self, key: str, **values) -> str:
        table = KO if self.language is AppLanguage.KOREAN else EN
        template = table.get(key) or EN.get(key) or key
        return template.format(**values) if values else template
