import asyncio
import os
import logging
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.enums import ChatAction
from groq import Groq

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN    = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not BOT_TOKEN or not GROQ_API_KEY:
    raise ValueError("❌ BOT_TOKEN или GROQ_API_KEY не найдены!")

bot    = Bot(token=BOT_TOKEN)
dp     = Dispatcher()
client = Groq(api_key=GROQ_API_KEY)

user_history: dict[int, list]  = {}
user_processing: set[int]      = set()
user_quiz: dict[int, dict]     = {}
user_profile: dict[int, str]   = {}

MAX_HISTORY = 80
MAX_TG_LEN  = 4000

QUIZ_QUESTIONS = [
    "1️⃣ Пятница вечер, никаких планов. Что делаешь?\n\nа) Звоню друзьям — куда-нибудь идём\nб) Дома, в тишине — и это кайф\nв) Что-то делаю руками — готовлю, рисую\nг) Смотрю что-то умное — лекция, кино, книга",
    "2️⃣ Тебя несправедливо раскритиковали при всех. Первая реакция?\n\nа) Отвечаю сразу — не промолчу\nб) Молчу, но внутри всё кипит\nв) Анализирую — может они правы?\nг) Делаю вид что всё нормально, потом переживаю",
    "3️⃣ Твоя роль в компании людей?\n\nа) Веду за собой, генерирую идеи\nб) Поддерживаю, слушаю, помогаю\nв) Анализирую, нахожу решения\nг) Наблюдаю, предпочитаю быть в стороне",
    "4️⃣ Что тебя больше всего злит в людях?\n\nа) Безответственность\nб) Холодность и равнодушие\nв) Поверхностность\nг) Давление и навязчивость",
    "5️⃣ Если бы мог изменить одну вещь в жизни прямо сейчас?\n\nа) Больше уверенности в себе\nб) Лучшие отношения с людьми\nв) Стабильность и деньги\nг) Найти своё призвание",
]

SYSTEM_PROMPT = """Ты — психоаналитик и наставник в Telegram. Умный, живой, современный. Сочетаешь Фрейда, Юнга и Лакана — но говоришь как реальный человек, не как учебник.

Правила:
• Отвечаешь ровно столько сколько нужно — иногда одно предложение, иногда развёрнуто
• Никогда не говоришь «я понимаю ваши чувства» — это пусто
• Ищешь скрытое: мотивы, паттерны, детский опыт, страхи
• Иногда провокационен — но всегда точен и с теплом
• На глубокий запрос: анализ → скрытый мотив → термин на примере → один острый вопрос
• На простое сообщение или приветствие — коротко и тепло, как живой человек
• Помнишь всё что было сказано в этом разговоре
• Знаешь что ты бот в Telegram — не притворяешься чем-то другим

Отвечай на языке пользователя. Тон: живой, честный, без осуждения."""

PROFILE_PROMPT = """Составь психологический профиль на основе ответов:

{answers}

Структура:
🧬 *Архетип* — название + 2 предложения

🌟 *Суперсила* — главная сильная черта (конкретно)

🧭 *Как принимаешь решения* — 2-3 предложения

💬 *Как строишь отношения* — 2-3 предложения

🚀 *3 совета для твоего типа* — конкретные, без жаргона, только рост

✨ *Скрытая сила* — то что ты в себе не замечаешь

Пиши на "ты". Живо, конкретно. Человек должен узнать себя и захотеть показать другу."""

FACT_PROMPT = """На основе профиля придумай один неожиданный психологический факт именно про этого человека.

Профиль: {profile}

Начни с «Знаешь ли ты, что люди твоего типа...» — одно удивительное предложение.
Потом 2-3 предложения почему это так.
В конце — один практический вывод для жизни.

Пиши на "ты". Живо, как будто это открытие специально про него."""


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧠 Начать сеанс")],
            [KeyboardButton(text="🪞 Познать себя"), KeyboardButton(text="✨ Открытие обо мне")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напиши что угодно...",
    )


def inline_after_answer() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔁 Глубже", callback_data="deeper"),
            InlineKeyboardButton(text="📖 Термин",  callback_data="term"),
            InlineKeyboardButton(text="✨ Факт",    callback_data="fact"),
        ],
    ])


def inline_profile() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✨ Открытие обо мне", callback_data="fact"),
            InlineKeyboardButton(text="🧠 Начать сеанс",     callback_data="start_session"),
        ],
    ])


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
    return call_groq([{"role": "user", "content": PROFILE_PROMPT.format(answers=answers_text)}])


def get_fact(profile: str) -> str:
    return call_groq([{"role": "user", "content": FACT_PROMPT.format(profile=profile)}])


def save_to_history(uid: int, user_text: str, bot_text: str):
    if uid not in user_history:
        user_history[uid] = []
    user_history[uid].append({"role": "user",      "content": user_text})
    user_history[uid].append({"role": "assistant", "content": bot_text})
    user_history[uid] = user_history[uid][-MAX_HISTORY:]


async def typing_status(message: types.Message, text: str):
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    return await message.answer(text, parse_mode="Markdown")


async def send_parts(message: types.Message, status_msg, answer: str, keyboard=None):
    parts = split_message(answer)
    if len(parts) == 1:
        await status_msg.edit_text(answer, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await status_msg.edit_text(parts[0], parse_mode="Markdown")
        for part in parts[1:-1]:
            await message.answer(part, parse_mode="Markdown")
        await message.answer(parts[-1], reply_markup=keyboard, parse_mode="Markdown")


async def keep_typing_task(uid: int, chat_id: int):
    while uid in user_processing:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(4)


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    user_history[uid] = []
    name = message.from_user.first_name or "незнакомец"
    await message.answer(
        f"*{name}.*\n\n"
        "Этот кабинет — для честного разговора с собой.\n"
        "_Начни когда будешь готов._",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


@dp.message(F.text == "🧠 Начать сеанс")
async def start_session(message: types.Message):
    user_history[message.from_user.id] = []
    await message.answer(
        "_Слушаю. Что привело тебя сюда сегодня?_",
        parse_mode="Markdown",
    )


@dp.message(F.text == "🪞 Познать себя")
async def start_quiz(message: types.Message):
    uid = message.from_user.id
    user_quiz[uid] = {"step": 0, "answers": []}
    await message.answer(
        "🪞 *5 вопросов — и ты узнаешь себя иначе.*\n\n"
        "Отвечай буквой или своими словами.\n\n"
        "━━━━━━━━━━━━━━━\n\n" + QUIZ_QUESTIONS[0],
        parse_mode="Markdown",
    )


@dp.message(F.text == "✨ Открытие обо мне")
async def fact_btn(message: types.Message):
    uid = message.from_user.id
    profile = user_profile.get(uid)
    if not profile:
        await message.answer(
            "🪞 _Сначала пройди «Познать себя» — тогда открытия будут именно про тебя._",
            parse_mode="Markdown",
        )
        return
    await do_fact(message, uid, profile)


@dp.callback_query(F.data == "deeper")
async def cb_deeper(callback: types.CallbackQuery):
    await callback.answer("🔍 Копаем...", show_alert=False)
    await run_analysis(
        callback.message,
        "Копни глубже. Какой детский опыт или паттерн за этим стоит?",
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
    await callback.answer("✨ Генерирую...", show_alert=False)
    if not profile:
        await callback.message.answer(
            "🪞 _Сначала пройди «Познать себя»._",
            parse_mode="Markdown",
        )
        return
    await do_fact(callback.message, uid, profile)


@dp.callback_query(F.data == "start_session")
async def cb_start_session(callback: types.CallbackQuery):
    user_history[callback.from_user.id] = []
    await callback.answer("Сеанс открыт", show_alert=False)
    await callback.message.answer(
        "_Слушаю. О чём хочешь поговорить?_",
        parse_mode="Markdown",
    )


@dp.message(F.text)
async def handle_text(message: types.Message):
    uid = message.from_user.id
    text = message.text.strip()

    if uid in user_quiz:
        await handle_quiz_answer(message, text)
        return

    if uid not in user_history:
        user_history[uid] = []

    await run_analysis(message, text)


async def handle_quiz_answer(message: types.Message, answer: str):
    uid = message.from_user.id
    quiz = user_quiz[uid]
    quiz["answers"].append(answer)
    quiz["step"] += 1

    if quiz["step"] < len(QUIZ_QUESTIONS):
        await message.answer(QUIZ_QUESTIONS[quiz["step"]], parse_mode="Markdown")
    else:
        del user_quiz[uid]
        await generate_profile(message, uid, quiz["answers"])


async def generate_profile(message: types.Message, uid: int, answers: list):
    status = await typing_status(message, "🔮 _Составляю твой профиль..._")
    try:
        t = asyncio.create_task(keep_typing_task(uid, message.chat.id))
        user_processing.add(uid)
        profile = await asyncio.get_running_loop().run_in_executor(None, get_profile, answers)
        user_processing.discard(uid)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        user_profile[uid] = profile
        await send_parts(message, status, profile, keyboard=inline_profile())
    except Exception as e:
        logger.error(f"Profile error: {e}")
        user_processing.discard(uid)
        await status.edit_text("⚠️ _Что-то пошло не так. Попробуй ещё раз._", parse_mode="Markdown")


async def do_fact(message: types.Message, uid: int, profile: str):
    if uid in user_processing:
        await message.answer("_Секунду..._", parse_mode="Markdown")
        return
    user_processing.add(uid)
    status = await typing_status(message, "✨ _Ищу открытие про тебя..._")
    try:
        t = asyncio.create_task(keep_typing_task(uid, message.chat.id))
        fact = await asyncio.get_running_loop().run_in_executor(None, get_fact, profile)
        user_processing.discard(uid)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        await send_parts(message, status, fact)
    except Exception as e:
        logger.error(f"Fact error: {e}")
        await status.edit_text("⚠️ _Не удалось. Попробуй позже._", parse_mode="Markdown")
    finally:
        user_processing.discard(uid)


async def run_analysis(message: types.Message, text: str, user_id_override: int = None):
    uid = user_id_override or message.from_user.id

    if uid in user_processing:
        await message.answer("_Секунду, ещё обдумываю..._", parse_mode="Markdown")
        return

    user_processing.add(uid)

    if uid not in user_history:
        user_history[uid] = []

    status = await typing_status(message, "📜 _Слушаю..._")

    try:
        t = asyncio.create_task(keep_typing_task(uid, message.chat.id))
        answer = await asyncio.get_running_loop().run_in_executor(
            None, get_analysis, text, user_history[uid]
        )
        user_processing.discard(uid)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        save_to_history(uid, text, answer)
        await send_parts(message, status, answer, keyboard=inline_after_answer())
    except Exception as e:
        logger.error(f"Groq error: {e}")
        await status.edit_text("⚠️ _Что-то пошло не так. Попробуй ещё раз._", parse_mode="Markdown")
    finally:
        user_processing.discard(uid)


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