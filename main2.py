import os
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from collections import Counter

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes
)

# ========== НАСТРОЙКИ ==========
# ВСТАВЬТЕ СЮДА ВАШ ТОКЕН БОТА
TELEGRAM_BOT_TOKEN = "8290426226:AAHp1rOGsBjOL_0p1N8gS820-lXge7GRtdw"  # <-- ЗАМЕНИТЕ НА ВАШ ТОКЕН

# Список username администраторов, которым доступна команда /stats (без @)
ADMIN_USERNAMES = ["admin1", "admin2"]  # <-- Укажите свои username

# Разрешенные расширения файлов
ALLOWED_EXTENSIONS = {'.pdf', '.doc', '.docx', '.txt', '.rtf'}

# Список факультетов
FACULTIES = [
    "РФиКТ",
    "ФМО",
    "ЭКОНОМфак",
    "ЮрФак",
    "ФПМИ",
    "МехМат",
    "ИстФак",
    "Мгэи",
    "ХимФак",
    "БиоФак",
    "ФСК",
    "ЖурФак",
    "ГеоФак",
    "ФилФак",
    "Институт Бизнеса",
    "ТеоФак",
    "ВоенФак",
    "ФФСН"
]

# ========== КОНСТАНТЫ ==========
# Состояния для ConversationHandler
(
    FIO, FACULTY, PARTICIPATED, PHONE,
    CITY, MOTIVATION_LETTER, CONFIRM
) = range(7)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ========== РАБОТА С БАЗОЙ ДАННЫХ ==========
class Database:
    def __init__(self, db_path="applications.db"):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Создаёт таблицу, если её нет"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    fio TEXT NOT NULL,
                    faculty TEXT NOT NULL,
                    participated TEXT NOT NULL,
                    tg_username TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    city TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_type TEXT NOT NULL
                )
            """)
            # Индекс для быстрого поиска по факультету
            conn.execute("CREATE INDEX IF NOT EXISTS idx_faculty ON applications(faculty)")
        logger.info("База данных инициализирована")

    def add_application(self, data):
        """Добавляет новую заявку в БД"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO applications 
                (timestamp, fio, faculty, participated, tg_username, phone, city, file_name, file_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data['timestamp'],
                data['fio'],
                data['faculty'],
                data['participated'],
                data['tg_username'],
                data['phone'],
                data['city'],
                data['file_name'],
                data['file_type']
            ))

    def get_all_applications(self):
        """Возвращает все заявки (для экспорта)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM applications ORDER BY timestamp DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_statistics(self):
        """Возвращает статистику по факультетам и общее количество"""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            faculties = conn.execute("""
                SELECT faculty, COUNT(*) as count 
                FROM applications 
                GROUP BY faculty 
                ORDER BY count DESC
            """).fetchall()
            today = datetime.now().strftime("%Y-%m-%d")
            today_count = conn.execute(
                "SELECT COUNT(*) FROM applications WHERE timestamp LIKE ?",
                (f"{today}%",)
            ).fetchone()[0]
            return {
                'total': total,
                'faculties': dict(faculties),
                'today': today_count
            }


# ========== КЛАСС БОТА ==========
class RegistrationBot:
    def __init__(self, token: str):
        self.token = token
        self.db = Database()
        logger.info("Инициализация бота...")

        # Создаём папки для факультетов (для файлов)
        self.create_faculty_folders()

        try:
            self.application = Application.builder().token(token).build()
            logger.info("Приложение создано успешно")
            self.setup_handlers()
            logger.info("Обработчики настроены")
        except Exception as e:
            logger.error(f"Ошибка при инициализации бота: {e}")
            raise

    def create_faculty_folders(self):
        """Создание папок для каждого факультета (для файлов)"""
        try:
            base_dir = Path("applications")
            base_dir.mkdir(exist_ok=True)
            logger.info(f"Базовая папка создана: {base_dir}")

            for faculty in FACULTIES:
                faculty_dir = base_dir / faculty
                faculty_dir.mkdir(exist_ok=True)

            logger.info("Структура папок создана успешно")
        except Exception as e:
            logger.error(f"Ошибка при создании папок: {e}")
            raise

    def setup_handlers(self):
        """Настройка всех обработчиков"""
        # Conversation handler для регистрации
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', self.start)],
            states={
                FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_fio)],
                FACULTY: [CallbackQueryHandler(self.get_faculty)],
                PARTICIPATED: [CallbackQueryHandler(self.get_participated)],
                PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_phone)],
                CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_city)],
                MOTIVATION_LETTER: [
                    MessageHandler(filters.Document.ALL, self.get_motivation_letter)
                ],
                CONFIRM: [CallbackQueryHandler(self.confirm_registration)]
            },
            fallbacks=[CommandHandler('cancel', self.cancel)]
        )
        self.application.add_handler(conv_handler)

        self.application.add_handler(CommandHandler('help', self.help_command))
        self.application.add_handler(CommandHandler('stats', self.show_stats_protected))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Начало регистрации"""
        user = update.effective_user
        username = f"@{user.username}" if user.username else "Не указан"
        context.user_data['tg_username'] = username

        welcome_text = (
            f"👋 Здравствуйте, {user.first_name or 'друг'}!\n\n"
            "Добро пожаловать в систему регистрации!\n\n"
            "Я помогу вам подать заявку. Для начала, пожалуйста, "
            "введите ваше ФИО (полностью):\n\n"
            f"📱 Ваш Telegram: {username}"
        )
        await update.message.reply_text(welcome_text)
        return FIO

    async def get_fio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['fio'] = update.message.text

        # Клавиатура с факультетами (2 столбца)
        keyboard = []
        for i in range(0, len(FACULTIES), 2):
            row = []
            if i < len(FACULTIES):
                row.append(InlineKeyboardButton(FACULTIES[i], callback_data=f"fac_{i}"))
            if i + 1 < len(FACULTIES):
                row.append(InlineKeyboardButton(FACULTIES[i + 1], callback_data=f"fac_{i + 1}"))
            keyboard.append(row)

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "🎓 Выберите желаемый факультет:",
            reply_markup=reply_markup
        )
        return FACULTY

    async def get_faculty(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        faculty_index = int(query.data.split('_')[1])
        context.user_data['faculty'] = FACULTIES[faculty_index]

        keyboard = [
            [InlineKeyboardButton("✅ Да", callback_data="part_yes"),
             InlineKeyboardButton("❌ Нет", callback_data="part_no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"✅ Выбран факультет: {FACULTIES[faculty_index]}\n\n"
            "📋 Участвовали ли вы в этом проекте раньше?",
            reply_markup=reply_markup
        )
        return PARTICIPATED

    async def get_participated(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        participated = query.data.split('_')[1]
        context.user_data['participated'] = "Да" if participated == "yes" else "Нет"

        await query.edit_message_text("📞 Пожалуйста, введите ваш номер телефона:")
        return PHONE

    async def get_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['phone'] = update.message.text
        await update.message.reply_text("🏙️ Пожалуйста, введите ваш город проживания:")
        return CITY

    async def get_city(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['city'] = update.message.text
        allowed_formats = ", ".join([ext.upper() for ext in ALLOWED_EXTENSIONS])
        await update.message.reply_text(
            f"📄 Пожалуйста, прикрепите файл с мотивационным письмом\n\n"
            f"📎 Разрешенные форматы: {allowed_formats}\n"
            f"⛔ Фотографии не принимаются!"
        )
        return MOTIVATION_LETTER

    async def get_motivation_letter(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message.document:
            allowed_formats = ", ".join([ext.upper() for ext in ALLOWED_EXTENSIONS])
            await update.message.reply_text(
                f"⚠️ Пожалуйста, прикрепите файл (документ), а не фотографию.\n"
                f"📎 Разрешенные форматы: {allowed_formats}"
            )
            return MOTIVATION_LETTER

        file = update.message.document
        file_name = file.file_name or "без_названия"
        file_ext = os.path.splitext(file_name)[1].lower()

        # Проверка формата
        if file_ext not in ALLOWED_EXTENSIONS:
            allowed_formats = ", ".join([ext.upper() for ext in ALLOWED_EXTENSIONS])
            await update.message.reply_text(
                f"❌ Неверный формат файла!\n"
                f"Вы отправили: {file_name} (формат {file_ext.upper()})\n\n"
                f"📎 Разрешенные форматы: {allowed_formats}\n"
                f"Пожалуйста, прикрепите файл в одном из этих форматов."
            )
            return MOTIVATION_LETTER

        context.user_data['file_id'] = file.file_id
        context.user_data['file_name'] = file_name
        context.user_data['file_ext'] = file_ext
        context.user_data['file_type'] = 'document'

        summary = (
            "📋 Пожалуйста, проверьте введенные данные:\n\n"
            f"👤 ФИО: {context.user_data['fio']}\n"
            f"🎓 Факультет: {context.user_data['faculty']}\n"
            f"📝 Участвовал ранее: {context.user_data['participated']}\n"
            f"📱 Telegram: {context.user_data['tg_username']}\n"
            f"📞 Телефон: {context.user_data['phone']}\n"
            f"🏙️ Город: {context.user_data['city']}\n"
            f"📎 Файл: {file_name}\n"
            f"📄 Формат: {file_ext.upper()}\n"
        )

        keyboard = [
            [InlineKeyboardButton("✅ Все верно", callback_data="confirm_yes"),
             InlineKeyboardButton("❌ Исправить", callback_data="confirm_no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(summary, reply_markup=reply_markup)
        return CONFIRM

    async def confirm_registration(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if query.data == "confirm_yes":
            try:
                await self.save_application(update, context)
                await query.edit_message_text(
                    "🎉 Регистрация успешно завершена!\n\n"
                    "✅ Ваша заявка сохранена.\n"
                    "📁 Файл мотивационного письма загружен.\n\n"
                    "Спасибо за участие! О результатах вам сообщат."
                )
            except Exception as e:
                logger.error(f"Ошибка при сохранении данных: {e}")
                await query.edit_message_text(
                    "⚠️ Произошла ошибка при сохранении данных. "
                    "Пожалуйста, попробуйте снова или обратитесь к администратору."
                )
        else:
            await query.edit_message_text(
                "❌ Регистрация отменена. Чтобы начать заново, отправьте /start"
            )
        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "❌ Регистрация отменена. Чтобы начать заново, отправьте /start"
        )
        return ConversationHandler.END

    async def save_application(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_data = context.user_data

        # Сохраняем файл
        file = await context.bot.get_file(user_data['file_id'])
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_fio = "".join(c for c in user_data['fio'] if c.isalnum() or c in (' ', '_')).rstrip()
        filename = f"{timestamp}_{safe_fio}{user_data['file_ext']}"

        faculty_dir = Path("applications") / user_data['faculty']
        faculty_dir.mkdir(exist_ok=True)
        file_path = faculty_dir / filename
        await file.download_to_drive(file_path)

        # Сохраняем данные в БД
        db_data = {
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'fio': user_data['fio'],
            'faculty': user_data['faculty'],
            'participated': user_data['participated'],
            'tg_username': user_data['tg_username'],
            'phone': user_data['phone'],
            'city': user_data['city'],
            'file_name': filename,
            'file_type': user_data['file_ext']
        }
        self.db.add_application(db_data)

        logger.info(f"✅ Новая заявка: {user_data['fio']}, факультет {user_data['faculty']}, файл {filename}")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        allowed_formats = ", ".join([ext.upper() for ext in ALLOWED_EXTENSIONS])
        help_text = (
            "📋 Доступные команды:\n\n"
            "/start - начать регистрацию\n"
            "/help - показать это сообщение\n"
            "/stats - показать статистику заявок (только для администраторов)\n\n"
            "📝 Информация о регистрации:\n"
            "1. Бот автоматически определит ваш Telegram\n"
            f"2. Принимаются только файлы: {allowed_formats}\n"
            "3. Фотографии не принимаются\n\n"
            "Во время регистрации вы можете отправить /cancel для отмены."
        )
        await update.message.reply_text(help_text)

    async def show_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Внутренняя функция для вывода статистики (без проверки прав)"""
        stats = self.db.get_statistics()
        if stats['total'] == 0:
            await update.message.reply_text("📭 Заявок пока нет!")
            return

        text = "📊 СТАТИСТИКА ЗАЯВОК\n\n"
        for faculty, count in stats['faculties'].items():
            text += f"🎓 {faculty}: {count} заявок\n"
        text += f"\n📈 Всего заявок: {stats['total']}"
        text += f"\n📅 Заявок сегодня: {stats['today']}"
        await update.message.reply_text(text)

    async def show_stats_protected(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Защищённая версия /stats, доступная только администраторам"""
        user = update.effective_user
        if user.username and user.username in ADMIN_USERNAMES:
            await self.show_stats(update, context)
        else:
            await update.message.reply_text("⛔ У вас нет прав на просмотр статистики.")

    def run(self):
        print("=" * 50)
        print("🤖 TELEGRAM БОТ ДЛЯ СБОРА ЗАЯВОК (SQLite)")
        print("=" * 50)
        print(f"🔑 Токен: {self.token[:10]}...")
        print("🔄 Запуск бота...")
        print("📱 Откройте Telegram и найдите вашего бота")
        print("📝 Отправьте команду /start для начала регистрации")
        print("=" * 50)
        try:
            self.application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        except KeyboardInterrupt:
            print("\n👋 Бот остановлен")
        except Exception as e:
            print(f"❌ Ошибка: {e}")


# ========== ФУНКЦИИ ДЛЯ КОНСОЛИ ==========
def view_statistics():
    """Просмотр статистики заявок из консоли"""
    db = Database()
    stats = db.get_statistics()
    if stats['total'] == 0:
        print("📭 Заявок пока нет!")
        return

    print(f"\n{'=' * 60}")
    print("СТАТИСТИКА ЗАЯВОК".center(60))
    print(f"{'=' * 60}")
    for faculty, count in stats['faculties'].items():
        print(f"🎓 {faculty}: {count} заявок")
    print(f"\n📈 Всего заявок: {stats['total']}")
    print(f"📅 Заявок сегодня: {stats['today']}")
    print(f"{'=' * 60}")


def export_to_excel():
    """Экспорт всех данных в Excel"""
    try:
        from openpyxl import Workbook
    except ImportError:
        print("❌ Для экспорта в Excel установите openpyxl: pip install openpyxl")
        return

    db = Database()
    applications = db.get_all_applications()
    if not applications:
        print("❌ Нет данных для экспорта")
        return

    wb = Workbook()
    ws = wb.active
    ws.title = "Все заявки"

    headers = ['ID', 'Дата регистрации', 'ФИО', 'Факультет', 'Участвовал ранее',
               'Telegram', 'Телефон', 'Город', 'Файл', 'Тип файла']
    ws.append(headers)

    for app in applications:
        ws.append([
            app['id'],
            app['timestamp'],
            app['fio'],
            app['faculty'],
            app['participated'],
            app['tg_username'],
            app['phone'],
            app['city'],
            app['file_name'],
            app['file_type']
        ])

    excel_file = "applications_summary.xlsx"
    wb.save(excel_file)
    print(f"\n✅ Данные экспортированы в {excel_file}")
    print(f"📊 Всего заявок в экспорте: {len(applications)}")


# ========== ГЛАВНОЕ МЕНЮ ==========
def main():
    print("=" * 60)
    print("🤖 ТЕЛЕГРАМ БОТ ДЛЯ СБОРА ЗАЯВОК (SQLite)".center(60))
    print("=" * 60)

    if TELEGRAM_BOT_TOKEN == "":
        print("❌ ОШИБКА: Вы не указали токен бота!")
        print("Вставьте токен в переменную TELEGRAM_BOT_TOKEN в коде.")
        return

    while True:
        print("\n" + "=" * 40)
        print("ГЛАВНОЕ МЕНЮ")
        print("=" * 40)
        print("1. 🚀 Запустить бота")
        print("2. 📊 Просмотреть статистику заявок")
        print("3. 📁 Экспортировать данные в Excel")
        print("4. ❌ Выйти")
        print("=" * 40)

        choice = input("Выберите действие (1-4): ").strip()
        if choice == "1":
            try:
                bot = RegistrationBot(TELEGRAM_BOT_TOKEN)
                bot.run()
            except Exception as e:
                print(f"❌ Ошибка при запуске бота: {e}")
        elif choice == "2":
            view_statistics()
        elif choice == "3":
            export_to_excel()
        elif choice == "4":
            print("👋 До свидания!")
            break
        else:
            print("❌ Неверный выбор. Попробуйте снова.")


if __name__ == '__main__':
    # Создаём папки для файлов, если их нет
    base_dir = Path("applications")
    if not base_dir.exists():
        base_dir.mkdir(exist_ok=True)
        for faculty in FACULTIES:
            (base_dir / faculty).mkdir(exist_ok=True)
        print("✅ Структура папок создана")

    main()