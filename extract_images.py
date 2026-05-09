#!/usr/bin/env python3
"""
استخراج لینک تمام تصاویر یک صفحه وب و ذخیره در فایل links.txt

نحوه استفاده:
    python extract_images.py <URL>

ویژگی‌ها:
    - اعتبارسنجی آدرس
    - دریافت صفحه با شبیه‌سازی مرورگر
    - پشتیبانی از تلاش مجدد در صورت timeout یا خطاهای 5xx (شامل 522 Cloudflare)
    - پارس HTML و استخراج src تگ‌های img
    - حذف data URI ها و لینک‌های تکراری
    - تبدیل لینک‌های نسبی به مطلق
    - خروجی فایل متنی UTF-8
"""

import sys
import time
import urllib.parse
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

# تلاش برای استفاده از lxml (سریع و دقیق)، در غیر این صورت html.parser
try:
    import lxml  # noqa: F401
    DEFAULT_PARSER = "lxml"
except ImportError:
    DEFAULT_PARSER = "html.parser"

# ------------------------------------------------------------------------------
# تنظیمات اصلی
# ------------------------------------------------------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
}
REQUEST_TIMEOUT = 45          # ثانیه (افزایش برای تحمل کندی سرور)
MAX_RETRIES = 3               # تعداد تلاش‌های مجاز
RETRY_BACKOFF = 5.0           # ثانیه تأخیر بین تلاش‌ها
OUTPUT_FILE = "links.txt"

# ------------------------------------------------------------------------------
def validate_url(raw_url: str) -> str:
    """
    بررسی اولیه ساختار URL و اطمینان از داشتن scheme
    در صورت نداشتن scheme، http:// پیش‌فرض افزوده می‌شود.
    """
    if not raw_url.startswith(("http://", "https://")):
        raw_url = "http://" + raw_url

    parsed = urllib.parse.urlparse(raw_url)
    if not parsed.scheme or not parsed.netloc:
        print(
            f"خطا: آدرس '{raw_url}' معتبر نیست (scheme یا netloc ندارد).",
            file=sys.stderr,
        )
        sys.exit(1)

    return raw_url


def fetch_page(
    url: str,
    session: requests.Session,
    retries: int = MAX_RETRIES,
    backoff: float = RETRY_BACKOFF,
) -> requests.Response:
    """
    دریافت صفحه با مدیریت خطاهای شبکه، timeout، کدهای 5xx (مانند 522 Cloudflare) و تلاش مجدد.

    Args:
        url: آدرس کامل صفحه
        session: نشست requests
        retries: تعداد کل تلاش‌ها
        backoff: تأخیر بین تلاش‌ها (ثانیه)

    Returns:
        requests.Response در صورت موفقیت نهایی

    Raises:
        SystemExit: اگر تمام تلاش‌ها شکست بخورد
    """
    for attempt in range(1, retries + 1):
        try:
            response = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            # ---- موفقیت ----
            if response.status_code == 200:
                return response

            # ---- خطاهای 5xx (موقتی) - شامل 522 Cloudflare - تلاش مجدد ----
            if response.status_code >= 500:
                print(
                    f"تلاش {attempt}/{retries}: کد وضعیت {response.status_code} "
                    f"(خطای سرور). در انتظار {backoff} ثانیه...",
                    file=sys.stderr,
                )
                time.sleep(backoff)
                continue  # تلاش بعدی

            # ---- سایر کدهای غیر 200 - خطای غیرقابل بازیابی ----
            print(
                f"خطا: سرور برای '{url}' کد وضعیت {response.status_code} برگرداند. "
                f"(منتظر 200 بود)",
                file=sys.stderr,
            )
            sys.exit(1)

        except requests.exceptions.Timeout:
            print(
                f"تلاش {attempt}/{retries} با timeout مواجه شد. "
                f"در انتظار {backoff} ثانیه...",
                file=sys.stderr,
            )
            time.sleep(backoff)

        except requests.exceptions.TooManyRedirects:
            print(
                f"خطا: تعداد تغییر مسیرهای '{url}' بیش از حد مجاز است.",
                file=sys.stderr,
            )
            sys.exit(1)

        except requests.exceptions.ConnectionError as e:
            print(
                f"خطا: اتصال به '{url}' برقرار نشد: {e}",
                file=sys.stderr,
            )
            sys.exit(1)

        except Exception as e:
            print(f"خطای ناشناخته در دریافت صفحه: {e}", file=sys.stderr)
            sys.exit(1)

    # اگر به اینجا برسیم یعنی همه retry ها تمام شده
    print(
        f"خطا: پس از {retries} تلاش، دریافت '{url}' موفق نبود.",
        file=sys.stderr,
    )
    sys.exit(1)


def extract_image_urls(html: str, base_url: str) -> List[str]:
    """
    پارس HTML و استخراج لینک‌های مطلق تصاویر.

    - لینک‌های data: رد می‌شوند.
    - لینک‌های تکراری حذف می‌شوند (با حفظ ترتیب).
    - srcهای خالی یا ناموجود نادیده گرفته می‌شوند.
    """
    soup = BeautifulSoup(html, DEFAULT_PARSER)
    img_tags = soup.find_all("img")

    if not img_tags:
        return []

    found_urls = []
    for img in img_tags:
        src = img.get("src")
        if not src or not isinstance(src, str):
            continue

        # حذف data URI ها
        if src.strip().startswith("data:"):
            continue

        # تبدیل نسبی به مطلق
        absolute_src = urllib.parse.urljoin(base_url, src)
        found_urls.append(absolute_src)

    # حذف تکراری و حفظ ترتیب
    seen = set()
    filtered = []
    for u in found_urls:
        if u not in seen:
            seen.add(u)
            filtered.append(u)

    return filtered


def write_links(file_path: str, links: List[str]) -> None:
    """نوشتن لینک‌ها در فایل متنی (هر خط یک لینک، UTF-8)"""
    with open(file_path, "w", encoding="utf-8") as f:
        for link in links:
            f.write(link + "\n")


def main() -> None:
    if len(sys.argv) != 2:
        print("کاربرد: python extract_images.py <URL>", file=sys.stderr)
        sys.exit(1)

    raw_url = sys.argv[1]
    validated_url = validate_url(raw_url)

    session = requests.Session()

    # دریافت صفحه با تلاش مجدد
    response = fetch_page(validated_url, session)

    # استخراج تصاویر
    image_links = extract_image_urls(response.text, response.url)

    if not image_links:
        print("خطا: هیچ تصویری در صفحه یافت نشد.", file=sys.stderr)
        sys.exit(1)

    write_links(OUTPUT_FILE, image_links)

    print(f"استخراج با موفقیت انجام شد: {len(image_links)} تصویر پیدا و ذخیره شد.")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"خطای پیش‌بینی‌نشده: {e}", file=sys.stderr)
        sys.exit(1)
