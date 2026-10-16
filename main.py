import asyncio
import json
import logging
import sys
from os import getenv
from pathlib import Path

from MoodleClient import MoodleClient

from aiogram import Bot, Dispatcher, F, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    FSInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

import splits
from constants import TOKEN, BASE_DIR, CONFIG_FILE

dp = Dispatcher()

# ---------- Estado en memoria ----------
configs: dict[int, dict] = {}

# NUEVO: {user_id: {"chat_id": int, "message_id": int}} → último mensaje de lista
ls_messages: dict[int, dict] = {}

DEFAULT_CONFIG = {
    "user": None,
    "passw": None,
    "host": None,
    "repo": None,
    "uptype": None,
    "splits": None,
}

async def telegram_upload(message,file):
        
    await bot.send_document(
        chat_id=message.chat.id,
        document=FSInputFile(file),
        caption=f"✅ {file.name}",
    )

async def processFile(file, user_id, message):
    file_size = file.stat().st_size     
    cfg = get_config(user_id)
    max_file_size = 1024 * 1024 * cfg["splits"]
    path_user = get_path_user(message.from_user.id)
    file_upload_count = 0

    if file_size > max_file_size:
        await message.answer(f"{file.name}, tamaño: {file_size}, límite: {max_file_size}")
                                
        parts = split.split_files(
            str(file),
            part_size_bytes=max_file_size,
            output_dir=str(path_user)
        )        
        
        for file in parts:
            await telegram_upload(message,file)
    else:
        await telegram_upload(message,file)
                                      

# ---------- Persistencia ----------
def load_configs() -> None:
    global configs
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            configs = {int(k): v for k, v in raw.items()}
        except Exception:
            logging.exception("No se pudo cargar configs.json")


def save_configs() -> None:
    try:
        CONFIG_FILE.write_text(
            json.dumps(configs, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        logging.exception("No se pudo guardar configs.json")


def get_config(user_id: int) -> dict:
    if user_id not in configs:
        configs[user_id] = dict(DEFAULT_CONFIG)
    for k, v in DEFAULT_CONFIG.items():
        configs[user_id].setdefault(k, v)
    return configs[user_id]


# ---------- Helpers generales ----------
def get_path_user(user_id: int) -> Path:
    path = BASE_DIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse_args(message: Message) -> str | None:
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return None
    return parts[1].strip()


def human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    unidades = ["KB", "MB", "GB", "TB", "PB", "EB"]
    size = float(num_bytes)
    for unidad in unidades:
        size /= 1024.0
        if size < 1024.0:
            return f"{size:.2f} {unidad}"
    return f"{size:.2f} EB"


def list_user_files(user_id: int) -> list[Path]:
    path_user = get_path_user(user_id)
    return sorted(f for f in path_user.iterdir() if f.is_file())


# ---------- Constructores de texto ----------
def build_ls_text(user_id: int) -> str:
    files = list_user_files(user_id)

    if not files:
        return "📂 No tienes archivos."

    lines = ["📂 <b>Archivos:</b>"]
    for i, f in enumerate(files):
        size = human_size(f.stat().st_size)
        lines.append(f"/up_{i} — {html.quote(f.name)} — <code>{size}</code>")

    lines.append("")
    lines.append(
        "Comandos: <code>/up_N</code> · <code>/rm_N</code> · "
        "<code>/rename N nombre</code>"
    )
    return "\n".join(lines)


def build_config_text(user_id: int) -> str:
    cfg = get_config(user_id)
    lineas = ["⚙️ <b>Tu configuración:</b>"]
    for key, val in cfg.items():
        val_mostrar = "—" if val is None else str(val)  # passw visible tal cual
        lineas.append(f"<b>{key}</b>: <code>{html.quote(val_mostrar)}</code>")
    return "\n".join(lineas)


# ---------- Teclados inline ----------
def kb_start() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📂 Archivos", callback_data="menu_archivos"),
                InlineKeyboardButton(text="⚙️ Cuenta", callback_data="menu_cuenta"),
            ]
        ]
    )


def kb_volver() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Volver al inicio", callback_data="menu_start")]
        ]
    )


def kb_archivos() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Refrescar", callback_data="menu_archivos")],
            [InlineKeyboardButton(text="🔙 Volver al inicio", callback_data="menu_start")],
        ]
    )


# ---------- Seguimiento del mensaje de lista ----------
def record_ls_message(user_id: int, message: Message) -> None:
    ls_messages[user_id] = {
        "chat_id": message.chat.id,
        "message_id": message.message_id,
    }


async def refresh_ls(user_id: int, bot: Bot) -> bool:
    """Edita el último mensaje de lista si existe. Devuelve True si lo hizo."""
    ref = ls_messages.get(user_id)
    if not ref:
        return False
    try:
        await bot.edit_message_text(
            chat_id=ref["chat_id"],
            message_id=ref["message_id"],
            text=build_ls_text(user_id),
            reply_markup=kb_archivos(),
        )
        return True
    except Exception as e:
        logging.debug("No se pudo refrescar /ls: %s", e)
        return False


# ---------- Comandos /set_* ----------
@dp.message(Command("set_user"))
async def set_user(message: Message) -> None:
    value = _parse_args(message)
    if value is None:
        await message.answer("Uso: <code>/set_user &lt;usuario&gt;</code>")
        return
    get_config(message.from_user.id)["user"] = value
    save_configs()
    await message.answer(f"✅ <b>user</b> = <code>{html.quote(value)}</code>")


@dp.message(Command("set_passw"))
async def set_passw(message: Message) -> None:
    value = _parse_args(message)
    if value is None:
        await message.answer("Uso: <code>/set_passw &lt;contraseña&gt;</code>")
        return
    get_config(message.from_user.id)["passw"] = value
    save_configs()
    await message.answer("✅ <b>passw</b> guardado.")


@dp.message(Command("set_host"))
async def set_host(message: Message) -> None:
    value = _parse_args(message)
    if value is None:
        await message.answer("Uso: <code>/set_host &lt;host&gt;</code>")
        return
    get_config(message.from_user.id)["host"] = value
    save_configs()
    await message.answer(f"✅ <b>host</b> = <code>{html.quote(value)}</code>")


@dp.message(Command("set_repo"))
async def set_repo(message: Message) -> None:
    value = _parse_args(message)
    if value is None:
        await message.answer("Uso: <code>/set_repo &lt;int&gt;</code>")
        return
    try:
        value_int = int(value)
    except ValueError:
        await message.answer("⚠️ <b>repo</b> debe ser un número entero.")
        return
    get_config(message.from_user.id)["repo"] = value_int
    save_configs()
    await message.answer(f"✅ <b>repo</b> = <code>{value_int}</code>")


@dp.message(Command("set_uptype"))
async def set_uptype(message: Message) -> None:
    value = _parse_args(message)
    if value is None:
        await message.answer("Uso: <code>/set_uptype &lt;tipo&gt;</code>")
        return
    get_config(message.from_user.id)["uptype"] = value
    save_configs()
    await message.answer(f"✅ <b>uptype</b> = <code>{html.quote(value)}</code>")


@dp.message(Command("set_splits"))
async def set_splits(message: Message) -> None:
    value = _parse_args(message)
    if value is None:
        await message.answer("Uso: <code>/set_splits &lt;int&gt;</code>")
        return
    try:
        value_int = int(value)
    except ValueError:
        await message.answer("⚠️ <b>splits</b> debe ser un número entero.")
        return
    get_config(message.from_user.id)["splits"] = value_int
    save_configs()
    await message.answer(f"✅ <b>splits</b> = <code>{value_int}</code>")


# ---------- /config y /unset ----------
@dp.message(Command("config"))
async def show_config(message: Message) -> None:
    await message.answer(
        build_config_text(message.from_user.id),
        reply_markup=kb_volver(),
    )


@dp.message(Command("unset"))
async def unset_cmd(message: Message) -> None:
    value = _parse_args(message)
    if value is None:
        await message.answer(
            "Uso: <code>/unset &lt;clave&gt;</code>\n"
            "Claves: user, passw, host, repo, uptype, splits"
        )
        return
    cfg = get_config(message.from_user.id)
    if value not in cfg:
        await message.answer(f"⚠️ Clave desconocida: <code>{html.quote(value)}</code>")
        return
    cfg[value] = None
    save_configs()
    await message.answer(f"🗑️ <b>{html.quote(value)}</b> borrado.")


# ---------- /start ----------
@dp.message(CommandStart())
async def start_cmd(message: Message) -> None:
    await message.answer(
        f"Hola, {html.bold(message.from_user.full_name)}! 👋\n\n"
        "Comandos rápidos:\n"
        "• /ls — listar archivos\n"
        "• /config — ver tu configuración\n"
        "• /set_user, /set_passw, /set_host, /set_repo, /set_uptype, /set_splits\n"
        "• /up_N — enviar archivo N\n"
        "• /rm_N — borrar archivo N\n"
        "• /rename N nombre — renombrar archivo N\n\n"
        "Elige una opción:",
        reply_markup=kb_start(),
    )


# ---------- Callback de menús ----------
@dp.callback_query(F.data.in_({"menu_start", "menu_archivos", "menu_cuenta"}))
async def cb_menu(callback: CallbackQuery) -> None:
    data = callback.data
    user_id = callback.from_user.id

    if data == "menu_start":
        texto = (
            f"Hola, {html.bold(callback.from_user.full_name)}! 👋\n\n"
            "Elige una opción:"
        )
        kb = kb_start()
    elif data == "menu_archivos":
        texto = build_ls_text(user_id)
        kb = kb_archivos()
        record_ls_message(user_id, callback.message)
    elif data == "menu_cuenta":
        texto = build_config_text(user_id)
        kb = kb_volver()
    else:
        await callback.answer("Opción desconocida")
        return

    try:
        await callback.message.edit_text(texto, reply_markup=kb)
    except Exception:
        # "message is not modified" u otros errores leves → ignorar
        pass

    await callback.answer()


# ---------- Subida de archivos ----------
@dp.message(F.photo | F.audio | F.video | F.voice)
async def upload_media(message: Message, bot: Bot) -> None:
    path_user = get_path_user(message.from_user.id)

    if message.photo:
        media = message.photo[-1]
        filename = f"photo_{media.file_unique_id}.jpg"
    elif message.audio:
        media = message.audio
        filename = Path(message.audio.file_name or f"audio_{media.file_unique_id}.mp3").name
    elif message.video:
        media = message.video
        filename = Path(message.video.file_name or f"video_{media.file_unique_id}.mp4").name
    else:
        media = message.voice
        filename = f"voice_{media.file_unique_id}.ogg"

    dest = path_user / filename
    await message.answer(f"⏳ Descargando <b>{html.quote(filename)}</b>...")
    await bot.download(media, destination=dest)
    await message.answer(f"✅ Guardado: <b>{html.quote(filename)}</b>")

    # Refrescar la lista si estaba abierta
    await refresh_ls(message.from_user.id, bot)


# ---------- /ls ----------
@dp.message(Command("ls"))
async def ls_cmd(message: Message) -> None:
    sent = await message.answer(
        build_ls_text(message.from_user.id),
        reply_markup=kb_archivos(),
    )
    record_ls_message(message.from_user.id, sent)


# ---------- /up_N ----------
@dp.message(F.text.regexp(r"^/up_(\d+)$"))
async def up_cmd(message: Message, bot: Bot) -> None:
    match = message.text.rsplit("_", 1)[1]
    try:
        index = int(match)
        files = list_user_files(message.from_user.id)
        file = files[index]
    except (ValueError, IndexError):
        await message.answer("⚠️ Índice inválido. Usa /ls para ver los disponibles.")
        return

    await message.answer(f"⏳ Subiendo: {html.quote(file.name)}")
    await processFile(file, user_id, message)


# ---------- /rm_N ----------
@dp.message(F.text.regexp(r"^/rm_(\d+)$"))
async def rm_cmd(message: Message, bot: Bot) -> None:
    match = message.text.rsplit("_", 1)[1]
    try:
        index = int(match)
        files = list_user_files(message.from_user.id)
        file = files[index]
    except (ValueError, IndexError):
        await message.answer("⚠️ Índice inválido. Usa /ls para ver los disponibles.")
        return

    nombre = file.name
    try:
        file.unlink()
    except OSError as e:
        await message.answer(f"❌ No se pudo borrar: {e}")
        return

    # Refrescar la lista si existía
    actualizado = await refresh_ls(message.from_user.id, bot)
    if not actualizado:
        sent = await message.answer(
            build_ls_text(message.from_user.id),
            reply_markup=kb_archivos(),
        )
        record_ls_message(message.from_user.id, sent)

    await message.answer(f"🗑️ Borrado: <b>{html.quote(nombre)}</b>")


# ---------- /rename N nombre ----------
@dp.message(F.text.regexp(r"^/rename\s+(\d+)\s+(.+)$"))
async def rename_cmd(message: Message, bot: Bot) -> None:
    partes = message.text.split(maxsplit=2)
    if len(partes) < 3:
        await message.answer("Uso: <code>/rename &lt;índice&gt; &lt;nuevo_nombre&gt;</code>")
        return

    try:
        index = int(partes[1])
        nuevo_nombre = Path(partes[2].strip()).name
    except (ValueError, IndexError):
        await message.answer("Uso: <code>/rename &lt;índice&gt; &lt;nuevo_nombre&gt;</code>")
        return

    if not nuevo_nombre:
        await message.answer("⚠️ El nuevo nombre no puede estar vacío.")
        return

    try:
        files = list_user_files(message.from_user.id)
        file = files[index]
    except (ValueError, IndexError):
        await message.answer("⚠️ Índice inválido. Usa /ls para ver los disponibles.")
        return

    path_user = get_path_user(message.from_user.id)
    destino = path_user / nuevo_nombre
    if destino.exists():
        await message.answer(
            f"⚠️ Ya existe un archivo llamado <code>{html.quote(nuevo_nombre)}</code>."
        )
        return

    nombre_viejo = file.name
    try:
        file.rename(destino)
    except OSError as e:
        await message.answer(f"❌ No se pudo renombrar: {e}")
        return

    # Refrescar la lista si existía
    actualizado = await refresh_ls(message.from_user.id, bot)
    if not actualizado:
        sent = await message.answer(
            build_ls_text(message.from_user.id),
            reply_markup=kb_archivos(),
        )
        record_ls_message(message.from_user.id, sent)

    await message.answer(
        f"✏️ <b>{html.quote(nombre_viejo)}</b> → <b>{html.quote(nuevo_nombre)}</b>"
    )


# ---------- Echo final ----------
@dp.message()
async def echo_handler(message: Message) -> None:
    try:
        await message.send_copy(chat_id=message.chat.id)
    except TypeError:
        await message.answer("Nice try!")


# ---------- main ----------
async def main() -> None:
    if not TOKEN:
        raise RuntimeError("Define la variable de entorno BOT_TOKEN")
    load_configs()
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())