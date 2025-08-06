import logging
import time
import requests
from bs4 import BeautifulSoup
from telegram import Update, Bot, ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, filters, CallbackContext
import threading
import random
import json
import os
from urllib.parse import urlparse, parse_qs
from textwrap import shorten
from telegram.error import TelegramError, TimedOut, Unauthorized, BadRequest
from datetime import datetime, timedelta
from telegram.ext import PreCheckoutQueryHandler
from telegram import ParseMode
from telegram import LabeledPrice

LINKS_FILE = 'user_links.json'

PAYMENT_REMINDER_FILE = "last_payment_reminder.txt"
PAYMENT_REMINDER_DAYS = 25

SEEN_FILE = 'seen_ads.json'
ACCESS_FILE = "user_access.json"
MAX_SEEN_PER_USER = 1000

ADMIN_FILE = "admin_ids.json"
ADMIN_IDS = []

def load_admin_ids():
    global ADMIN_IDS
    if not os.path.exists(ADMIN_FILE):
        with open(ADMIN_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        ADMIN_IDS = []
        return

    try:
        with open(ADMIN_FILE, "r", encoding="utf-8") as f:
            ADMIN_IDS = json.load(f)
    except Exception as e:
        logging.warning(f"⚠️ admin_ids.json поврежден. Пересоздаю: {e}")
        ADMIN_IDS = []
        with open(ADMIN_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)


def save_admin_ids():
    with open(ADMIN_FILE, "w", encoding="utf-8") as f:
        json.dump(ADMIN_IDS, f)


TELEGRAM_TOKEN = '7748447384:AAGQLEPntaC55u_HyOJw20BNBNG0an_NISY'
PAYMENT_TOKEN = '390540012:LIVE:69831'  # токен yookassa
CHECK_INTERVAL = 300  
CLEANUP_INTERVAL_DAYS = 1  
SEEN_ADS_MAX_AGE_DAYS = 30


user_links = {}  # {user_id: [url1, url2, ...]}
seen_ads = {}    # {user_id: {ad_id1, ad_id2, ...}}
user_states = {} # {user_id: state_string}
user_temp_data = {} # {user_id: {step_data}}
user_access = {}
link_balance = {}


avito_city_codes = {
    "москва": "moskva",
    "санкт-петербург": "sankt-peterburg",
    "казань": "kazan",
    "новосибирск": "novosibirsk",
    "екатеринбург": "ekaterinburg",
    "нижний новгород": "nizhniy_novgorod",
    "самара": "samara",
    "ростов-на-дону": "rostov-na-donu",
    "челябинск": "chelyabinsk",
    "омск": "omsk"
}


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 11; SM-A107F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Mobile Safari/537.36"
]


logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

LINK_EXPIRY_FILE = "link_expiry.json"
link_expiry = {}   

def load_link_expiry():
    global link_expiry
    if not os.path.exists(LINK_EXPIRY_FILE):
        with open(LINK_EXPIRY_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        link_expiry = {}
        return

    try:
        with open(LINK_EXPIRY_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            link_expiry = {int(k): v for k, v in raw.items()}
    except Exception as e:
        logging.warning(f"⚠️ link_expiry.json поврежден. Пересоздаю: {e}")
        link_expiry = {}
        with open(LINK_EXPIRY_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)

def save_link_expiry():
    with open(LINK_EXPIRY_FILE, "w", encoding="utf-8") as f:
        json.dump(link_expiry, f, ensure_ascii=False, indent=2)

def save_link_balance():
    with open("link_balance.json", "w") as f:
        json.dump(link_balance, f)

def load_link_balance():
    global link_balance
    if os.path.exists("link_balance.json"):
        with open("link_balance.json") as f:
            link_balance = {int(k): v for k, v in json.load(f).items()}

def load_user_links():
    global user_links
    if os.path.exists(LINKS_FILE):
        try:
            with open(LINKS_FILE, 'r', encoding='utf-8') as f:
                user_links = json.load(f)
                user_links = {int(k): v for k, v in user_links.items()}
                logging.info("✅ user_links загружены из файла.")
        except Exception as e:
            logging.error(f"Ошибка при загрузке user_links: {e}")

def save_user_links():
    try:
        with open(LINKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_links, f, ensure_ascii=False, indent=2)
        logging.info("💾 user_links сохранены в файл.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении user_links: {e}")

def save_user_access():
    with open(ACCESS_FILE, "w", encoding="utf-8") as f:
        json.dump(user_access, f, ensure_ascii=False)

def load_user_access():
    global user_access
    if not os.path.exists(ACCESS_FILE):
        
        with open(ACCESS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        user_access = {}
        return

    try:
        with open(ACCESS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            user_access = {int(k): v for k, v in raw.items()}
    except Exception as e:
        logging.warning(f"⚠️ user_access.json поврежден. Пересоздаю: {e}")
        user_access = {}
        with open(ACCESS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
            
def precheckout_callback(update: Update, context: CallbackContext):
    query = update.pre_checkout_query
    if query.invoice_payload.startswith("subscription_"):
        query.answer(ok=True)
    else:
        query.answer(ok=False, error_message="Ошибка. Попробуйте позже.")

def load_seen_ads():
    global seen_ads
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
                seen_ads = {int(k): v for k, v in raw.items()}  # {user_id: {ad_id: date_str}}
                logging.info("✅ seen_ads загружены из файла.")
        except Exception as e:
            logging.error(f"Ошибка при загрузке seen_ads: {e}")

def save_seen_ads():
    try:
        with open(SEEN_FILE, 'w', encoding='utf-8') as f:
            json.dump(seen_ads, f, ensure_ascii=False, indent=2)
        logging.info("💾 seen_ads сохранены в файл.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении seen_ads: {e}")

def check_and_clean_user_links_if_too_large():
    max_size_mb = 100
    if os.path.exists(LINKS_FILE):
        size_mb = os.path.getsize(LINKS_FILE) / (1024 * 1024)
        if size_mb > max_size_mb:
            logging.warning(f"⚠️ Файл {LINKS_FILE} превышает {max_size_mb} МБ ({size_mb:.2f} МБ). Создаю резервную копию...")


            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = f"user_links_backup_{timestamp}.json"
            try:
                with open(LINKS_FILE, 'r', encoding='utf-8') as original, \
                     open(backup_file, 'w', encoding='utf-8') as backup:
                    backup.write(original.read())
                logging.info(f"✅ Резервная копия сохранена как {backup_file}")
            except Exception as e:
                logging.error(f"Ошибка при создании резервной копии: {e}")


            with open(LINKS_FILE, 'w', encoding='utf-8') as f:
                json.dump({}, f)
            global user_links
            user_links = {}
            logging.info("🧹 user_links очищены из файла и памяти.")

def grant_admin_access(update: Update, context: CallbackContext):
    requester_id = update.message.chat_id

    if requester_id not in ADMIN_IDS:
        update.message.reply_text("⛔ У тебя нет прав для этой команды.")
        return

    args = context.args
    if not args:
        update.message.reply_text("Использование: /grant_admin [user_id]")
        return

    try:
        target_id = int(args[0])
        if target_id not in ADMIN_IDS:
            ADMIN_IDS.append(target_id)
            save_admin_ids()
        user_access[target_id] = "2099-12-31"
        save_user_access()
        update.message.reply_text(f"✅ Пользователю {target_id} выданы права администратора и бессрочный доступ.")
    except Exception as e:
        update.message.reply_text(f"❌ Ошибка: {e}")

def revoke_admin_access(update: Update, context: CallbackContext):
    requester_id = update.message.chat.id

    if requester_id not in ADMIN_IDS:
        update.message.reply_text("⛔ У тебя нет прав для этой команды.")
        return

    args = context.args
    if not args:
        update.message.reply_text("Использование: /revoke_admin [user_id]")
        return

    try:
        target_id = int(args[0])

        if target_id == requester_id:
            update.message.reply_text("⛔ Нельзя отзывать свои собственные права администратора.")
            return

        if target_id in ADMIN_IDS:
            ADMIN_IDS.remove(target_id)
            save_admin_ids()

        if target_id in user_access:
            del user_access[target_id]
            save_user_access()

        update.message.reply_text(f"✅ Права администратора и доступ пользователя {target_id} отозваны.")
    except Exception as e:
        update.message.reply_text(f"❌ Ошибка: {e}")


def loop_notify_payment(bot: Bot, admin_id: int):
    while True:
        check_and_notify_payment(bot, admin_id)
        time.sleep(86400)  


def check_and_notify_payment(bot: Bot, admin_id: int):
    file_path = "last_payment_reminder.txt"


    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            try:
                last_sent = datetime.strptime(f.read().strip(), "%Y-%m-%d")
            except:
                last_sent = None
    else:
        last_sent = None

    now = datetime.now()

    if not last_sent or (now - last_sent).days >= 25:
        try:
            bot.send_message(chat_id=admin_id, text="💳 Напоминание: пора оплатить сервер/хостинг.")
            with open(file_path, "w") as f:
                f.write(now.strftime("%Y-%m-%d"))
            logging.info("✅ Отправлено напоминание об оплате.")
        except Exception as e:
            logging.warning(f"❌ Ошибка при отправке напоминания: {e}")



def start_payment_period(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    text = update.message.text

    options = {
        "📅 1 месяц – 5000 ₽": (5000, 30),
        "📅 3 месяца – 13990 ₽": (13990, 90),
        "📅 12 месяцев - 33333 ₽": (33333, 365)
    }

    if text not in options:
        update.message.reply_text("Неверный выбор тарифа.")
        return

    amount, days = options[text]
    context.user_data["payment_days"] = days

    context.bot.send_invoice(
        chat_id,
        title=f"Подписка на {text}",
        description=f"Доступ на {days} дней",
        payload=f"subscription_{chat_id}",
        provider_token=PAYMENT_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(f"Подписка на {text}", amount * 100)],
        start_parameter="access-subscription"
    )

def successful_payment_callback(update: Update, context: CallbackContext):
    user_id = update.message.chat_id
    days = context.user_data.get("payment_days", 30)
    now = datetime.now()

    payload = update.message.successful_payment.invoice_payload

    if payload.startswith("link_slot_"):
        link_balance[user_id] = link_balance.get(user_id, 0) + 1
        save_link_balance()
        update.message.reply_text("✅ Слот добавлен! Теперь вы можете добавить одну ссылку.")
        return
    
    # Учитываем продление
    current = user_access.get(user_id)
    if current:
        try:
            current_date = datetime.strptime(current, "%Y-%m-%d")
            if current_date > now:
                now = current_date
        except:
            pass

    until = now + timedelta(days=days)
    user_access[user_id] = until.strftime("%Y-%m-%d")
    save_user_access()

    update.message.reply_text(f"✅ Подписка активна до {until.date()}")


def has_active_subscription(user_id):
    global ADMIN_IDS  
    if user_id in ADMIN_IDS:
        return True
    date_str = user_access.get(user_id)
    if not date_str:
        return False
    try:
        return datetime.strptime(date_str, "%Y-%m-%d") >= datetime.now()
    except:
        return False


def parse_avito(url, pages=3):
    session = requests.Session()
    session.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://www.avito.ru/"
    })
    
    ads = []
    for page in range(1, pages + 1):
        page_url = f"{url}&p={page}" if "?" in url else f"{url}?p={page}"
        try:
            response = session.get(page_url, timeout=10)
            logging.info(f"[Avito] {page_url} → {response.status_code}")

            if response.status_code == 429:
                logging.warning("⚠️ Получен HTTP 429 Too Many Requests. Avito блокирует запросы.")
                time.sleep(60)  
                break

            if response.status_code != 200:
                logging.error(f"❌ Ошибка при запросе {page_url}: статус {response.status_code}")
                continue

            soup = BeautifulSoup(response.text, 'html.parser')

            for item in soup.select('[data-marker="item"]'):
                title_tag = item.select_one('[itemprop="name"]')
                link_tag = item.select_one('a')
                price_tag = item.select_one('[itemprop="price"]')
                image_tag = item.select_one('img')
                desc_tag = item.select_one('[data-marker="item-description"]')

                if not (title_tag and link_tag):
                    continue

                title = title_tag.text.strip()
                link = 'https://www.avito.ru' + link_tag['href']
                price = price_tag['content'] if price_tag else '0'
                image = image_tag['src'] if image_tag and image_tag.has_attr('src') else None
                desc = desc_tag.text.strip() if desc_tag else ''

                try:
                    price_int = int(price)
                except ValueError:
                    price_int = 0

                ad_id = link.split('_')[-1]

                ads.append({
                    'id': ad_id,
                    'title': title,
                    'link': link,
                    'price': price_int,
                    'image': image,
                    'desc': desc
                })
                
            time.sleep(random.uniform(1, 2.5))
            
        except requests.RequestException as e:
            logging.error(f"Исключение при запросе {page_url}: {e}")
    return ads



def check_ads(bot: Bot):
    import statistics

    while True:
        for user_id, links in user_links.items():

            
            is_subscribed = has_active_subscription(user_id)
            active_links = []

            
            if not is_subscribed:
                expiry_map = link_expiry.get(user_id, {})
                for url in links:
                    expiry_str = expiry_map.get(url)
                    if expiry_str:
                        try:
                            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d")
                            if expiry_date >= datetime.now():
                                active_links.append(url)
                            else:
                                logging.info(f"⏱ Срок действия ссылки у пользователя {user_id} истёк: {url}")
                        except:
                            logging.warning(f"⚠️ Невалидная дата у ссылки {url} для пользователя {user_id}")
                    else:
                        logging.info(f"⏱ Нет информации о сроке действия ссылки {url} у пользователя {user_id}")
            else:
                active_links = links

            
            if not is_subscribed and not active_links:
                logging.info(f"⛔ Пользователь {user_id} без подписки и без активных ссылок — пропущен.")
                continue

            for url in links:
                try:
                    ads = parse_avito(url)
                    all_prices = [ad['price'] for ad in ads if ad['price'] > 0]
                    median_price = statistics.median(all_prices) if all_prices else 0

                    if user_id not in seen_ads:
                        seen_ads[user_id] = set()

                    for ad in ads:
                        try:
                            parsed_url = urlparse(url)
                            params = parse_qs(parsed_url.query)

                            if 'price' in params:
                                price_range = params['price'][0].split('-')
                                if len(price_range) == 2:
                                    min_str, max_str = price_range
                                    min_price = int(min_str) if min_str.isdigit() else 0
                                    max_price = int(max_str) if max_str.isdigit() else 9999999

                                    if not (min_price <= ad['price'] <= max_price):
                                        continue
                        except Exception as e:
                            logging.warning(f"Фильтрация по цене не сработала: {e}")

                        if ad['id'] not in seen_ads.get(user_id, {}):
                            seen_ads.setdefault(user_id, {})[ad['id']] = datetime.now().strftime("%Y-%m-%d")

                            
                            if len(seen_ads[user_id]) > MAX_SEEN_PER_USER:
                                seen_ads[user_id] = set(list(seen_ads[user_id])[-MAX_SEEN_PER_USER // 2:])
                            save_seen_ads()

                            
                            if median_price > 0:
                                if ad['price'] < median_price * 0.7:
                                    label = "🟢 Ниже рынка"
                                elif ad['price'] > median_price * 1.3:
                                    label = "🔴 Выше рынка"
                                else:
                                    label = "⚪ Рыночная цена"
                            else:
                                label = "⚪ Рыночная цена"

                            title = ad['title'][:100]
                            desc = ad['desc'][:300]
                            link = ad['link']
                            price = ad['price']

                            caption = (
                                f"📢 <b>{title}</b>\n"
                                f"💰 {price}₽ {label}\n"
                                f"📝 {desc}...\n"
                                f"🔗 {link}"
                            )

                            if len(caption) > 1024:
                                caption = caption[:1020] + "…"

                            image = ad.get('image')

                            try:
                                if image:
                                    bot.send_photo(chat_id=user_id, photo=image, caption=caption, parse_mode='HTML')
                                else:
                                    bot.send_message(chat_id=user_id, text=caption, parse_mode='HTML')
                            except Unauthorized:
                                logging.warning(f"Пользователь {user_id} заблокировал бота.")
                            except TimedOut:
                                logging.warning(f"Timeout при отправке пользователю {user_id}.")
                            except BadRequest as e:
                                logging.error(f"BadRequest ({user_id}): {e}")
                            except TelegramError as e:
                                logging.error(f"TelegramError ({user_id}): {e}")

                except Exception as e:
                    logging.error(f"Ошибка при обработке ссылок пользователя {user_id}: {e}")

        logging.info(f"🕒 Пауза {CHECK_INTERVAL} сек до следующей проверки...")
        time.sleep(CHECK_INTERVAL)






def main_menu():
    return ReplyKeyboardMarkup([
        ["🔍 Новый фильтр"],
        ["➕ Отследить ссылку"],
        ["📋 Мои ссылки", "🗑 Удалить ссылку"]
    ], resize_keyboard=True)


def start(update: Update, context: CallbackContext):
    user_id = update.message.chat_id

    if has_active_subscription(user_id) or link_balance.get(user_id, 0) > 0:
        update.message.reply_text(
            "Привет! Я помогу отслеживать объявления с Avito.\nВыбери действие:",
            reply_markup=main_menu()
        )
    else:
        update.message.reply_text(
            "👋 Привет! Я — Multi Poisk Bot!\n\n"
            "Я помогу тебе первым находить лучшие объявления на Avito по твоим критериям. "
            "Как только появится новое предложение — я мгновенно пришлю тебе уведомление с фото, описанием и ссылкой.\n\n"
            "🚀 Что я умею?\n"
            "✅ Мгновенные оповещения — объявления приходят сразу после публикации.\n"
            "✅ Гибкие фильтры — ищешь что-то конкретное? Настрой поиск по:\n"
            "   - Ключевым словам (например, \"iPhone 15\", \"квартира в Москве\")\n"
            "   - Ценовому диапазону (укажи min/max)\n"
            "   - Локации (город, район)\n"
            "✅ Удобный формат уведомлений — фото + краткое описание + ссылка.\n"
            "✅ Простое управление — всё через кнопки, никаких сложных команд!\n\n"
            "🔍 Как начать?\n"
            "1️⃣ Нажми ➕ «Добавить фильтр»\n"
            "2️⃣ Укажи параметры поиска (ключевые слова, цену, город)\n"
            "3️⃣ Получай уведомления — как только появится подходящее объявление, я сразу сообщу!\n\n"
            "📌 Пример уведомления:\n"
            "📱 iPhone 15 Pro 256GB\n"
            "💰 85 000 ₽\n"
            "📍 Москва, ЦАО\n"
            "🔗 [Смотреть на Avito](https://www.avito.ru/...)\n"
            "краткое описание\n"
            "---\n\n"
            "💎 Также у нас есть выгодные подписки!\n"
            "С ними ты сможешь добавлять до 7 фильтров в любых категориях Avito и находить ещё больше выгодных предложений.\n"
            "Хочешь узнать подробности? Нажми кнопку «Оплата» — там всё понятно описано!\n\n"
            "⚡ Чем быстрее настроишь фильтры — тем раньше начнёшь находить выгодные предложения!\n\n"
            "Нажми «➕ Добавить фильтр» и попробуй!\n\n"
            "📩 Появились вопросы? Напиши нам — и мы ответим на интересующий вопрос!\n"
            "@Beshimov\n\n"
            "<b>💳 Тарифы подписки:</b>\n"
            "📅 1 месяц – 5000 ₽\n"
            "📅 3 месяца – 13990 ₽\n"
            "📅 12 месяцев – 33333 ₽\n\n"
            "Можно также купить 1 слот для одного фильтра за 1000 ₽/мес.",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup(
                [["💳 Оплатить", "💳 Купить 1 фильтр"]],
                resize_keyboard=True
            )
        )


def handle_message(update: Update, context: CallbackContext):
    user_id = update.message.chat_id
    state = user_states.get(user_id)
    temp = user_temp_data.setdefault(user_id, {})
    text = update.message.text.strip().lower()

    
    
    if text == "💳 оплатить":
        plans = (
            "📆 <b>Планы подписки</b>:\n"
            "<pre>Срок       Цена</pre>\n"
            "<pre>1 месяц    5000 ₽</pre>\n"
            "<pre>3 месяца   13990 ₽</pre>\n"
            "<pre>12 месяцев 33333 ₽</pre>"
        )
        keyboard = ReplyKeyboardMarkup(
            [["📅 1 месяц – 5000 ₽"], ["📅 3 месяца – 13990 ₽"], ["📅 12 месяцев - 33333 ₽"]],
            resize_keyboard=True
        )
        update.message.reply_text(plans, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return
    
    if text in ["📅 1 месяц – 5000 ₽", "📅 3 месяца – 13990 ₽", "📅 12 месяцев - 33333 ₽"]:
        return start_payment_period(update, context)
    
    if text == "💳 купить 1 фильтр":
        context.bot.send_invoice(
            chat_id=user_id,
            title="Покупка слота",
            description="Оплата 1 слота для отслеживания ссылки",
            payload=f"link_slot_{user_id}",
            provider_token=PAYMENT_TOKEN,
            currency="RUB",
            prices=[LabeledPrice("Слот", 100000)],
            start_parameter="buy-link-slot"
        )
        return

    
    if not has_active_subscription(user_id) and link_balance.get(user_id, 0) <= 0:
        update.message.reply_text(
            "🚫 Подписка неактивна. Чтобы пользоваться ботом:\n\n"
            "<b>💳 Тарифы подписки:</b>\n"
            "📅 1 месяц – 5000 ₽\n"
            "📅 3 месяца – 13990 ₽\n"
            "📅 12 месяцев – 33333 ₽\n\n"
            "Или купите 1 фильтр за 1000 ₽.",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup(
                [["💳 Оплатить", "💳 Купить 1 фильтр"]],
                resize_keyboard=True
            )
        )
        return

    
    if state == 'deleting_link':
        links = user_links.get(user_id, [])
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(links):
                removed = links.pop(idx)
                save_user_links()
                update.message.reply_text(
                    f"🗑 Ссылка удалена:\n{removed}",
                    reply_markup=main_menu()
                )
                user_states[user_id] = None
                return
        update.message.reply_text(
            "⚠️ Неверный номер. Пожалуйста, отправь корректный номер ссылки.",
            reply_markup=main_menu()
        )
        return

    
    if state == 'awaiting_city':
        if text == "вся россия":
            temp['city'] = ''
        elif text in avito_city_codes:
            temp['city'] = avito_city_codes[text]
        else:
            temp['city'] = text.replace(" ", "")
        user_states[user_id] = 'awaiting_query'
        update.message.reply_text("🔎 Что ищем? (например: iPhone 13)")
        return

    elif state == 'awaiting_query':
        temp['query'] = text
        user_states[user_id] = 'awaiting_min_price'
        update.message.reply_text("💰 Минимальная цена? (или пропусти)")
        return

    elif state == 'awaiting_min_price':
        temp['price_min'] = text if text.isdigit() else ''
        user_states[user_id] = 'awaiting_max_price'
        update.message.reply_text("💰 Максимальная цена? (или пропусти)")
        return

    elif state == 'awaiting_max_price':
        temp['price_max'] = text if text.isdigit() else ''
        city = temp.get('city')
        query = temp.get('query', '').replace(" ", "+")
        pmin = temp.get('price_min')
        pmax = temp.get('price_max')

        price_part = ''
        if pmin and pmax:
            price_part = f"&price={pmin}-{pmax}"
        elif pmin:
            price_part = f"&price={pmin}-"
        elif pmax:
            price_part = f"&price=0-{pmax}"

        url = f"https://www.avito.ru/{city}?q={query}{price_part}"
        user_links.setdefault(user_id, []).append(url)
        save_user_links()
        seen_ads.setdefault(user_id, set())

        display_url = url if city else f"https://www.avito.ru/rossiya?q={query}{price_part}"
        update.message.reply_text(
            f"✅ Ссылка сформирована и добавлена в отслеживание:\n{display_url}",
            reply_markup=main_menu()
        )
        user_states[user_id] = None
        user_temp_data[user_id] = {}
        return

    
    if state == 'waiting_for_link':
        if 'avito.ru' not in text:
            update.message.reply_text("⚠️ Это не похоже на ссылку Avito. Попробуй ещё раз.")
            return

        links = user_links.setdefault(user_id, [])
        if text in links:
            update.message.reply_text("⚠️ Такая ссылка уже добавлена.")
            return

        if len(links) >= 7:
            update.message.reply_text("⚠️ Превышен лимит: максимум 7 ссылок.")
            return

        links.append(text)
        save_user_links()

        if not has_active_subscription(user_id):
            link_balance[user_id] = link_balance.get(user_id, 1) - 1
            save_link_balance()

            
            expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
            link_expiry.setdefault(user_id, {})[text] = expiry
            save_link_expiry()

        update.message.reply_text(f"✅ Ссылка добавлена:\n{text}", reply_markup=main_menu())
        user_states[user_id] = None
        return

    
    if text == "➕ отследить ссылку":
        user_states[user_id] = 'waiting_for_link'
        update.message.reply_text("🔗 Введите ссылку для отслеживания:")
        return

    elif text == "📋 мои ссылки":
        links = user_links.get(user_id, [])
        if links:
            msg = "🔗 Ваши отслеживаемые ссылки:\n" + "\n".join(f"{i}. {l}" for i, l in enumerate(links, 1))
            msg += "\n\nЧтобы удалить ссылку, нажмите 🗑 Удалить ссылку."
            if not has_active_subscription(user_id):
                balance = link_balance.get(user_id, 0)
                msg += f"\n\n💳 Слотов для добавления: {balance}"
            update.message.reply_text(msg, reply_markup=main_menu())
        else:
            update.message.reply_text("📭 У вас пока нет отслеживаемых ссылок.", reply_markup=main_menu())
        return

    elif text == "🗑 удалить ссылку":
        links = user_links.get(user_id, [])
        if not links:
            update.message.reply_text("У тебя нет ни одной ссылки для удаления.", reply_markup=main_menu())
        else:
            user_states[user_id] = 'deleting_link'
            link_list = '\n'.join(f"{i+1}. {l}" for i, l in enumerate(links))
            update.message.reply_text(
                f"🗑 Отправь номер ссылки, которую хочешь удалить:\n{link_list}",
                reply_markup=main_menu()
            )
        return

    elif text == "🔍 новый фильтр":
        user_states[user_id] = 'awaiting_city'
        user_temp_data[user_id] = {}
        update.message.reply_text(
            "🏙 В каком городе искать? Например: Москва, Санкт-Петербург или напиши 'вся Россия'"
        )
        return

    
    update.message.reply_text(
        "Я тебя не понял. Пожалуйста, используй кнопки. 😊",
        reply_markup=main_menu()
    )

def auto_cleanup():
    while True:
        now = datetime.now()

        
        cutoff = now - timedelta(days=30)
        removed_count = 0

        for user_id, ad_map in seen_ads.items():
            new_ads = {
                ad_id: date for ad_id, date in ad_map.items()
                if datetime.strptime(date, "%Y-%m-%d") >= cutoff
            }
            removed_count += len(ad_map) - len(new_ads)
            seen_ads[user_id] = new_ads

        if removed_count:
            save_seen_ads()
            logging.info(f"🧹 Удалено {removed_count} старых объявлений из seen_ads.json")

    
        for user_id, url_map in link_expiry.items():
            user_links_list = user_links.get(int(user_id), [])
            to_delete = []

            for url, expiry_str in url_map.items():
                try:
                    expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d")
                    if expiry_date < now and not has_active_subscription(int(user_id)):
                        to_delete.append(url)
                except:
                    continue

            for url in to_delete:
                if url in user_links_list:
                    user_links_list.remove(url)
                del url_map[url]

            if to_delete:
                logging.info(f"🗑 Удалены просроченные ссылки для пользователя {user_id}: {len(to_delete)}")

        save_link_expiry()
        save_user_links()

        time.sleep(CLEANUP_INTERVAL_DAYS * 86400)  # раз в день




def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("revoke_admin", revoke_admin_access))
    dp.add_handler(CommandHandler("grant_admin", grant_admin_access))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dp.add_handler(MessageHandler(Filters.regex("^📅 "), start_payment_period))
    dp.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    dp.add_handler(MessageHandler(Filters.successful_payment, successful_payment_callback))

    threading.Thread(target=auto_cleanup, daemon=True).start()
    threading.Thread(target=loop_notify_payment, args=(updater.bot, 5016274966), daemon=True).start()
    thread = threading.Thread(target=check_ads, args=(updater.bot,), daemon=True)
    thread.start()

    check_and_notify_payment(updater.bot, admin_id=5016274966)

    check_and_clean_user_links_if_too_large()
    load_link_balance()
    load_admin_ids()
    load_link_expiry()
    load_user_access()
    load_user_links()
    load_seen_ads()

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
