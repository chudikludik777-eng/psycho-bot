import asyncio
import os
import logging
import random
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.enums import ChatAction
from groq import Groq

# ─────────────────────────────────────────────
# Окружение
# ─────────────────────────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN    = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not BOT_TOKEN or not GROQ_API_KEY:
    raise ValueError("❌ BOT_TOKEN или GROQ_API_KEY не найдены! Проверь файл .env")

bot    = Bot(token=BOT_TOKEN)
dp     = Dispatcher()
client = Groq(api_key=GROQ_API_KEY)

# ─────────────────────────────────────────────
# Хранилище
# ─────────────────────────────────────────────
user_history: dict[int, list]    = {}
user_processing: set[int]        = set()
user_quiz: dict[int, dict]       = {}   # состояние опросника {user_id: {step, answers}}
user_profile: dict[int, str]     = {}   # готовый профиль пользователя
user_in_session: set[int]        = set() # открыт ли сеанс

MAX_HISTORY = 80
MAX_TG_LEN  = 4000

# ─────────────────────────────────────────────
# Вопросы опросника «Кто я?»
# ─────────────────────────────────────────────
QUIZ_QUESTIONS = [
    "1️⃣ *Представьте:* пятница вечер, никаких планов. Что вы делаете?\n\nа) Звоню друзьям — куда-нибудь идём\nб) Остаюсь дома, кайфую в тишине\nв) Что-то делаю руками — готовлю, рисую, чиню\nг) Смотрю что-то умное — лекция, кино, книга",

    "2️⃣ *Сценарий:* на работе/учёбе вас несправедливо раскритиковали при всех. Ваша первая реакция?\n\nа) Отвечаю сразу — не промолчу\nб) Молчу, но внутри всё кипит\nв) Анализирую — может они правы?\nг) Делаю вид что всё нормально, потом переживаю",

    "3️⃣ *Выберите:* какая роль вам ближе в группе людей?\n\nа) Я веду за собой — генерирую идеи\nб) Я поддерживаю — слушаю и помогаю\nв) Я анализирую — нахожу ошибки и решения\nг) Я наблюдаю — предпочитаю быть в стороне",

    "4️⃣ *Честно:* что вас больше всего злит в других людях?\n\nа) Безответственность и опоздания\nб) Холодность и равнодушие\nв) Глупость и поверхностность\nг) Давление и навязчивость",

    "5️⃣ *Последний вопрос:* если бы вы могли изменить одну вещь в своей жизни прямо сейчас — что это?\n\nа) Больше уверенности в себе\nб) Лучшие отношения с людьми\nв) Больше денег и стабильности\nг) Найти своё настоящее призвание",
]

# ─────────────────────────────────────────────
# Промпты
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """Ты — живой, тёплый психоаналитик и наставник. Ты сочетаешь в себе Фрейда, Юнга и Лакана, но говоришь как живой человек — не как робот.

Твои принципы:
• Каждый случай уникален — никаких шаблонов
• Ищешь скрытые мотивы: подавленные желания, детские паттерны, влияние родителей
• Говоришь глубоко, метафорично, иногда провокационно — но всегда в точку
• НИКОГДА не говоришь «я понимаю ваши чувства» — это банально
• Исследуешь тень личности (Юнг), защитные механизмы, переносы
• Задаёшь один острый вопрос в конце

Структура ответа на глубокий запрос:
1. 🔍 Анализ (2-3 абзаца)
2. 🌑 Скрытый мотив или детский паттерн
3. 📚 «Урок для будущего психолога» — один термин на примере
4. ❓ Один провокационный вопрос

• ИСКЛЮЧЕНИЕ: Если сообщение — простое приветствие или светская фраза — ответь коротко и тепло, как терапевт в начале сеанса. Без анализа.

Отвечай на том языке, на котором пишет пользователь.
Тон: живой, тёплый, без осуждения, честный."""

PROFILE_PROMPT = """На основе ответов пользователя в опроснике составь его психологический профиль.

Ответы: {answers}

Структура профиля:
🧬 *Ваш архетип* — название + 2 предложения кто это

🌟 *Ваша суперсила* — главная сильная черта личности (конкретно)

🧭 *Как вы принимаете решения* — 2-3 предложения

💬 *Как вы строите отношения* — 2-3 предложения

🚀 *3 совета для вашего типа личности* — конкретные, простые, без психологического жаргона. Не говори о слабостях — только о том как расти.

✨ *Ваша скрытая сила* — то чего человек в себе не замечает

Пиши живо, конкретно, без воды. Человек должен узнать себя и захотеть поделиться этим с другом."""

FACT_PROMPT = """На основе профиля пользователя придумай один неожиданный психологический факт именно про него.

Профиль: {profile}

Формат: «Знаете ли вы, что люди вашего типа...» — одно предложение, удивительное и точное. 
Потом 2-3 предложения объяснения почему это так.
В конце — практический вывод для жизни.

Пиши живо, как будто это открытие специально про этого человека."""

# ─────────────────────────────────────────────
# Клавиатуры
# ─────────────────────────────────────────────

def keyboard_no_session() -> ReplyKeyboardMarkup:
    """Кнопки когда сеанс не открыт"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧠 Начать сеанс"), KeyboardButton(text="🪞 Кто я?")],
            [KeyboardButton(text="✨ Случайный факт"), KeyboardButton(text="❓ Помощь")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напишите что-нибудь или выберите действие...",
    )


def keyboard_in_session() -> ReplyKeyboardMarkup:
    """Кнопки во время активного сеанса"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌿 Завершить сеанс"), KeyboardButton(text="❓ Помощь")],
            [KeyboardButton(text="🪞 Кто я?"), KeyboardButton(text="✨ Случайный факт")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Расскажите, что вас беспокоит...",
    )


def inline_after_answer() -> InlineKeyboardMarkup:
    """Инлайн под каждым ответом аналитика"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔁 Глубже", callback_data="deeper"),
            InlineKeyboardButton(text="📖 Термин",  callback_data="term"),
            InlineKeyboardButton(text="✨ Факт",    callback_data="fact"),
        ],
        [
            InlineKeyboardButton(text="🌿 Завершить сеанс", callback_data="clear"),
        ],
    ])


def inline_profile() -> InlineKeyboardMarkup:
    """Инлайн под профилем"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✨ Случайный факт обо мне", callback_data="fact"),
            InlineKeyboardButton(text="🧠 Начать сеанс",           callback_data="start_session"),
        ],
    ])


# ─────────────────────────────────────────────
# Утилиты
# ─────────────────────────────────────────────

def split_message(text: str, limit: int = MAX_TG_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts = []
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = text.rfind(" ", 0, limit)
        if split_at == -1:
            split_at = limit
        parts.append(text[:split_at].strip())
        text = text[split_at:].strip()
    if text:
        parts.append(text)
    return parts


def call_groq(messages: list) -> str:
    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        temperature=0.9,
        max_tokens=2500,
    )
    return completion.choices[0].message.content


def get_analysis(text: str, history: list) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history[-MAX_HISTORY:]
    messages.append({"role": "user", "content": text})
    return call_groq(messages)


def get_profile(answers: list) -> str:
    answers_text = "\n".join([f"Вопрос {i+1}: {a}" for i, a in enumerate(answers)])
    prompt = PROFILE_PROMPT.format(answers=answers_text)
    return call_groq([{"role": "user", "content": prompt}])


def get_fact(profile: str) -> str:
    prompt = FACT_PROMPT.format(profile=profile)
    return call_groq([{"role": "user", "content": prompt}])


def save_to_history(user_id: int, user_text: str, bot_text: str):
    if user_id not in user_history:
        user_history[user_id] = []
    user_history[user_id].append({"role": "user",      "content": user_text})
    user_history[user_id].append({"role": "assistant", "content": bot_text})
    user_history[user_id] = user_history[user_id][-MAX_HISTORY:]


async def send_typing_and_status(message: types.Message, status_text: str):
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    return await message.answer(status_text, parse_mode="Markdown")


async def send_parts(message: types.Message, status_msg, answer: str, keyboard=None):
    parts = split_message(answer)
    if len(parts) == 1:
        await status_msg.edit_text(answer, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await status_msg.edit_text(parts[0], parse_mode="Markdown")
        for part in parts[1:-1]:
            await message.answer(part, parse_mode="Markdown")
        await message.answer(parts[-1], reply_markup=keyboard, parse_mode="Markdown")


# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    user_history[uid] = []
    user_in_session.discard(uid)
    name = message.from_user.first_name or "незнакомец"
    await message.answer(
        f"*Добро пожаловать, {name}.*\n\n"
        "Здесь нет осуждения. Только честный взгляд внутрь.\n\n"
        "_Выберите с чего начать — или просто напишите что вас беспокоит._",
        parse_mode="Markdown",
        reply_markup=keyboard_no_session(),
    )


# ─────────────────────────────────────────────
# Кнопка «Начать сеанс»
# ─────────────────────────────────────────────

@dp.message(F.text == "🧠 Начать сеанс")
async def start_session(message: types.Message):
    uid = message.from_user.id
    user_history[uid] = []
    user_in_session.add(uid)
    await message.answer(
        "🛋 *Сеанс открыт.*\n\n"
        "Ложитесь на кушетку. Закройте глаза.\n"
        "_Что привело вас ко мне сегодня?_",
        parse_mode="Markdown",
        reply_markup=keyboard_in_session(),
    )


# ─────────────────────────────────────────────
# Кнопка «Завершить сеанс»
# ─────────────────────────────────────────────

@dp.message(F.text == "🌿 Завершить сеанс")
async def end_session(message: types.Message):
    uid = message.from_user.id
    user_history[uid] = []
    user_in_session.discard(uid)
    user_processing.discard(uid)
    await message.answer(
        "🌿 *Сеанс завершён.*\n\n"
        "_До следующей встречи.\nКогда будете готовы — я здесь._",
        parse_mode="Markdown",
        reply_markup=keyboard_no_session(),
    )


# ─────────────────────────────────────────────
# Кнопка «Кто я?» — запуск опросника
# ─────────────────────────────────────────────

@dp.message(F.text == "🪞 Кто я?")
async def start_quiz(message: types.Message):
    uid = message.from_user.id
    user_quiz[uid] = {"step": 0, "answers": []}
    await message.answer(
        "🪞 *Опросник «Кто я?»*\n\n"
        "Я задам вам 5 вопросов-сценариев.\n"
        "Отвечайте буквой или своими словами — как чувствуете.\n\n"
        "_В конце вы получите свой психологический профиль._\n\n"
        "━━━━━━━━━━━━━━━\n\n" + QUIZ_QUESTIONS[0],
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
# Кнопка «Случайный факт»
# ─────────────────────────────────────────────

@dp.message(F.text == "✨ Случайный факт")
async def random_fact(message: types.Message):
    uid = message.from_user.id
    profile = user_profile.get(uid)

    if not profile:
        await message.answer(
            "🪞 *Сначала пройдите опросник «Кто я?»*\n\n"
            "_Тогда факты будут именно про вас, а не общие._",
            parse_mode="Markdown",
        )
        return

    await do_fact(message, uid, profile)


# ─────────────────────────────────────────────
# Помощь
# ─────────────────────────────────────────────

@dp.message(F.text == "❓ Помощь")
@dp.message(Command("help"))
async def help_btn(message: types.Message):
    uid = message.from_user.id
    kb = keyboard_in_session() if uid in user_in_session else keyboard_no_session()
    await message.answer(
        "🧠 *Психоаналитический кабинет*\n\n"
        "Это место для честного разговора с собой.\n\n"
        "━━━━━━━━━━━━━━━\n\n"
        "🧠 *Начать сеанс* — открываем диалог, бот помнит контекст\n"
        "🪞 *Кто я?* — опросник, после которого вы получите свой психологический профиль\n"
        "✨ *Случайный факт* — неожиданное открытие про вас лично\n"
        "🌿 *Завершить сеанс* — закрываем и очищаем историю\n\n"
        "━━━━━━━━━━━━━━━\n\n"
        "_Или просто напишите что вас беспокоит — и мы начнём._",
        parse_mode="Markdown",
        reply_markup=kb,
    )


# ─────────────────────────────────────────────
# Inline кнопки
# ─────────────────────────────────────────────

@dp.callback_query(F.data == "clear")
async def cb_clear(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_history[uid] = []
    user_in_session.discard(uid)
    user_processing.discard(uid)
    await callback.answer("Сеанс завершён", show_alert=False)
    await callback.message.answer(
        "🌿 *Сеанс завершён.*\n\n_До следующей встречи. Когда будете готовы — я здесь._",
        parse_mode="Markdown",
        reply_markup=keyboard_no_session(),
    )


@dp.callback_query(F.data == "deeper")
async def cb_deeper(callback: types.CallbackQuery):
    await callback.answer("🔍 Копаем глубже...", show_alert=False)
    await run_analysis(
        callback.message,
        "Копни глубже. Какой детский опыт или ранний паттерн за этим стоит?",
        user_id_override=callback.from_user.id,
    )


@dp.callback_query(F.data == "term")
async def cb_term(callback: types.CallbackQuery):
    await callback.answer("📖 Ищу термин...", show_alert=False)
    await run_analysis(
        callback.message,
        "Объясни другой психоаналитический термин связанный с этой ситуацией. Выбери тот что ещё не упоминался.",
        user_id_override=callback.from_user.id,
    )


@dp.callback_query(F.data == "fact")
async def cb_fact(callback: types.CallbackQuery):
    uid = callback.from_user.id
    profile = user_profile.get(uid)
    await callback.answer("✨ Генерирую факт...", show_alert=False)
    if not profile:
        await callback.message.answer(
            "🪞 _Сначала пройдите опросник «Кто я?» — тогда факты будут именно про вас._",
            parse_mode="Markdown",
        )
        return
    await do_fact(callback.message, uid, profile)


@dp.callback_query(F.data == "start_session")
async def cb_start_session(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_history[uid] = []
    user_in_session.add(uid)
    await callback.answer("Сеанс открыт", show_alert=False)
    await callback.message.answer(
        "🛋 *Сеанс открыт.*\n\n_Что вы хотите исследовать сегодня?_",
        parse_mode="Markdown",
        reply_markup=keyboard_in_session(),
    )


# ─────────────────────────────────────────────
# Основной хэндлер текста
# ─────────────────────────────────────────────

@dp.message(F.text)
async def handle_text(message: types.Message):
    uid = message.from_user.id
    text = message.text.strip()

    # ── Если идёт опросник ──
    if uid in user_quiz:
        await handle_quiz_answer(message, text)
        return

    # ── Если сеанс не открыт — открываем автоматически ──
    if uid not in user_in_session:
        user_in_session.add(uid)
        if uid not in user_history:
            user_history[uid] = []

    await run_analysis(message, text)


# ─────────────────────────────────────────────
# Логика опросника
# ─────────────────────────────────────────────

async def handle_quiz_answer(message: types.Message, answer: str):
    uid = message.from_user.id
    quiz = user_quiz[uid]
    quiz["answers"].append(answer)
    quiz["step"] += 1

    if quiz["step"] < len(QUIZ_QUESTIONS):
        await message.answer(
            QUIZ_QUESTIONS[quiz["step"]],
            parse_mode="Markdown",
        )
    else:
        # Все вопросы пройдены — генерируем профиль
        del user_quiz[uid]
        await generate_profile(message, uid, quiz["answers"])


async def generate_profile(message: types.Message, uid: int, answers: list):
    status = await send_typing_and_status(message, "🔮 _Составляю ваш психологический профиль..._")

    try:
        async def keep_typing():
            while True:
                await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(keep_typing())

        profile = await asyncio.get_running_loop().run_in_executor(
            None, get_profile, answers
        )

        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

        user_profile[uid] = profile
        await send_parts(message, status, profile, keyboard=inline_profile())

    except Exception as e:
        logger.error(f"Profile error: {e}")
        await status.edit_text(
            "⚠️ _Что-то пошло не так. Попробуйте ещё раз._",
            parse_mode="Markdown",
        )


# ─────────────────────────────────────────────
# Генерация факта
# ─────────────────────────────────────────────

async def do_fact(message: types.Message, uid: int, profile: str):
    if uid in user_processing:
        await message.answer("_Одну секунду..._", parse_mode="Markdown")
        return

    user_processing.add(uid)
    status = await send_typing_and_status(message, "✨ _Ищу удивительный факт именно про вас..._")

    try:
        async def keep_typing():
            while uid in user_processing:
                await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(keep_typing())
        fact = await asyncio.get_running_loop().run_in_executor(None, get_fact, profile)
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

        await send_parts(message, status, fact)

    except Exception as e:
        logger.error(f"Fact error: {e}")
        await status.edit_text("⚠️ _Не удалось получить факт. Попробуйте позже._", parse_mode="Markdown")
    finally:
        user_processing.discard(uid)


# ─────────────────────────────────────────────
# Ядро анализа
# ─────────────────────────────────────────────

async def run_analysis(message: types.Message, text: str, user_id_override: int = None):
    uid = user_id_override or message.from_user.id

    if uid in user_processing:
        await message.answer(
            "_Одну минуту... Я ещё обдумываю ваши слова._",
            parse_mode="Markdown",
        )
        return

    user_processing.add(uid)

    if uid not in user_history:
        user_history[uid] = []

    status = await send_typing_and_status(message, "📜 _Разворачиваю свиток вашего подсознания..._")

    try:
        async def keep_typing():
            while uid in user_processing:
                await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(keep_typing())
        answer = await asyncio.get_running_loop().run_in_executor(
            None, get_analysis, text, user_history[uid]
        )
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

        save_to_history(uid, text, answer)
        await send_parts(message, status, answer, keyboard=inline_after_answer())

    except Exception as e:
        logger.error(f"Groq error: {e}")
        await status.edit_text(
            "⚠️ _Мой разум затуманен... Попробуйте переформулировать мысль._",
            parse_mode="Markdown",
        )
    finally:
        user_processing.discard(uid)


# ─────────────────────────────────────────────
# Запуск
# ─────────────────────────────────────────────

async def main():
    from aiohttp import web

    async def health(request):
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8000)))
    await site.start()

    logger.info("🚀 Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())