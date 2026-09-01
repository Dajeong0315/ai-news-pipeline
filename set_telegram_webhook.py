"""Vercel 배포 완료 후, 텔레그램 봇의 webhook URL을 등록한다.

실행:
    python set_telegram_webhook.py https://<your-project>.vercel.app/webhook
"""

import sys

import telegram_client


def main():
    if len(sys.argv) != 2:
        print("사용법: python set_telegram_webhook.py https://<your-project>.vercel.app/webhook")
        sys.exit(1)
    url = sys.argv[1]
    result = telegram_client.set_webhook(url)
    print(result)


if __name__ == "__main__":
    main()
