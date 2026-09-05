import re
import unicodedata
from typing import Optional


def normalize_whitespace(text: Optional[str]) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"\r\n|\r", "\n", normalized)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in normalized.split("\n")]
    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def normalize_email(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    cleaned = email.strip().lower()
    match = re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", cleaned)
    if match:
        return match.group(0)
    return None


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    if cleaned.startswith("+61"):
        cleaned = "0" + cleaned[3:]
    elif cleaned.startswith("61") and len(cleaned) == 11:
        cleaned = "0" + cleaned[2:]
        
    if len(cleaned) == 10 and cleaned.startswith("04"):
        return f"{cleaned[:4]} {cleaned[4:7]} {cleaned[7:]}"
    elif len(cleaned) == 10:
        return f"{cleaned[:2]} {cleaned[2:6]} {cleaned[6:]}"
    
    return phone.strip()


def extract_domain(email_or_url: Optional[str]) -> Optional[str]:
    if not email_or_url:
        return None
    cleaned = email_or_url.strip().lower()
    if "@" in cleaned:
        return cleaned.split("@")[-1].strip()
    match = re.search(r"(?:https?://)?(?:www\.)?([a-z0-9.-]+\.[a-z]{2,})", cleaned)
    if match:
        return match.group(1)
    return None


def normalize_company_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    cleaned = normalize_whitespace(name)
    cleaned = re.sub(r"\bpty\.?\s*ltd\.?", "Pty Ltd", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bltd\.?", "Ltd", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\binc\.?", "Inc", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()
