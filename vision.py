import os
import json
import base64
import asyncio
import requests
from dotenv import load_dotenv


load_dotenv()

API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("MODEL_NAME", "openrouter/free")

API_URL = "https://openrouter.ai/api/v1/chat/completions"


PROMPT = """
Ты распознаёшь фотографию кассового чека.

Верни ТОЛЬКО корректный JSON без markdown, без ``` и без дополнительных комментариев.

Формат:

{
  "store": "Название магазина",
  "total": 1234.56,
  "items": [
    {
      "name": "Название товара",
      "price": 123.45
    }
  ]
}

Правила:

1. store — название магазина с чека.
2. total — итоговая сумма чека как число.
3. items — список всех товаров, которые удалось распознать.
4. price — цена конкретного товара как число.
5. Не добавляй валюту к числам.
6. Если какой-то товар невозможно прочитать, постарайся определить его по контексту.
7. Не выдумывай товары и цены.
8. Ответ должен быть только JSON.
"""


def _send_request(payload):
    response = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90,
    )

    return response


def _parse_json(text):
    """
    Пытаемся получить JSON даже если модель случайно
    добавила лишний текст или markdown.
    """

    text = text.strip()

    # Убираем markdown-обёртку
    text = text.replace("```json", "")
    text = text.replace("```", "")
    text = text.strip()

    # Первая попытка — весь ответ
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Вторая попытка — найти JSON между первой { и последней }
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("Модель не вернула JSON")

    json_text = text[start:end + 1]

    return json.loads(json_text)


def _validate_result(data):
    """
    Проверяем, что ответ модели действительно похож
    на распознанный чек.
    """

    if not isinstance(data, dict):
        raise ValueError("Ответ модели имеет неправильный формат")

    if "store" not in data:
        data["store"] = "Неизвестный магазин"

    if "total" not in data:
        raise ValueError("Модель не распознала итоговую сумму")

    if "items" not in data:
        data["items"] = []

    try:
        data["total"] = float(data["total"])
    except (TypeError, ValueError):
        raise ValueError("Итоговая сумма имеет неправильный формат")

    if not isinstance(data["items"], list):
        data["items"] = []

    cleaned_items = []

    for item in data["items"]:
        if not isinstance(item, dict):
            continue

        if "name" not in item or "price" not in item:
            continue

        try:
            price = float(item["price"])
        except (TypeError, ValueError):
            continue

        cleaned_items.append({
            "name": str(item["name"]),
            "price": price
        })

    data["items"] = cleaned_items

    return data


def recognize_receipt(image_path):
    """
    Распознаёт чек по фотографии через OpenRouter.

    Возвращает:

    {
        "store": "...",
        "total": 123.45,
        "items": [...]
    }
    """

    if not API_KEY:
        raise ValueError("OPENROUTER_API_KEY не найден в .env")

    if not os.path.exists(image_path):
        raise FileNotFoundError(
            f"Файл чека не найден: {image_path}"
        )

    # Читаем фотографию
    with open(image_path, "rb") as image_file:
        image_bytes = image_file.read()

    # Кодируем в base64
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": PROMPT
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                f"data:image/jpeg;base64,"
                                f"{image_base64}"
                            )
                        }
                    }
                ]
            }
        ]
    }

    # Делаем несколько попыток.
    # Это особенно полезно для бесплатных моделей,
    # у которых бывают временные rate limit.
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):

        try:
            print(
                f"🤖 Попытка распознавания "
                f"{attempt}/{max_attempts}..."
            )

            response = _send_request(payload)

            # Успешный ответ
            if response.status_code == 200:

                result = response.json()

                text = result["choices"][0]["message"]["content"]

                data = _parse_json(text)

                data = _validate_result(data)

                print("✅ Чек успешно распознан")

                return data

            # Временный лимит
            if response.status_code == 429:

                try:
                    error_data = response.json()

                    retry_after = (
                        error_data
                        .get("error", {})
                        .get("metadata", {})
                        .get("retry_after_seconds", 10)
                    )

                except Exception:
                    retry_after = 10

                retry_after = min(int(retry_after), 30)

                if attempt < max_attempts:
                    print(
                        f"⏳ Модель перегружена. "
                        f"Повтор через {retry_after} сек."
                    )

                    # Так как recognize_receipt пока
                    # синхронная функция, используем sleep.
                    import time
                    time.sleep(retry_after)

                    continue

                raise Exception(
                    "Бесплатная модель временно перегружена. "
                    "Попробуйте отправить чек ещё раз через минуту."
                )

            # Любая другая ошибка API
            raise Exception(
                f"OpenRouter вернул ошибку "
                f"{response.status_code}: {response.text}"
            )

        except requests.Timeout:

            if attempt < max_attempts:
                print("⏳ Таймаут. Повторяем...")
                continue

            raise Exception(
                "Модель слишком долго обрабатывала чек. "
                "Попробуйте отправить фотографию ещё раз."
            )

        except requests.RequestException as e:

            if attempt < max_attempts:
                print(f"🌐 Ошибка соединения: {e}")
                continue

            raise Exception(
                "Не удалось связаться с сервисом распознавания."
            )

        except (KeyError, ValueError, json.JSONDecodeError) as e:

            print(f"⚠️ Ошибка обработки ответа модели: {e}")

            if attempt < max_attempts:
                continue

            raise Exception(
                "Модель вернула неправильный формат данных."
            )

    raise Exception("Не удалось распознать чек.")