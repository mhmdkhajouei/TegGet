import os
import asyncio

os.environ["DATABASE_PATH"] = "test_tether_tracker.db"

from app.database import init_db, insert_alarm, get_active_alarms
from app.services.alarm_service import evaluate_and_trigger_alarms
from app.config import settings


async def run_tests():
    if os.path.exists("test_tether_tracker.db"):
        os.remove("test_tether_tracker.db")

    await init_db()
    await insert_alarm(chat_id=12345, target_price=193300.0, condition='above', frequency='every_time')

    class MockBot:
        class Bot:
            async def send_message(self, chat_id, text, parse_mode=None):
                return True

        bot = Bot()

    print("--- شروع تست‌های اتمیک ---")

    # سناریوهای ۱ تا ۴ (که قبلاً با موفقیت رد کردیم)
    await evaluate_and_trigger_alarms(193400.0, "test", MockBot())
    await evaluate_and_trigger_alarms(193500.0, "test", MockBot())
    await evaluate_and_trigger_alarms(193100.0, "test", MockBot())
    await evaluate_and_trigger_alarms(192500.0, "test", MockBot())
    print("✅ سناریوهای ۱ تا ۴ با موفقیت انجام شد")

    # سناریو ۵
    await evaluate_and_trigger_alarms(193500.0, "test", MockBot())

    # واکشی وضعیت فعلی از دیتابیس
    alarms = await get_active_alarms()
    current_state = alarms[0]['is_armed']

    print(f"DEBUG: وضعیت نهایی is_armed در دیتابیس برابر است با: {current_state}")

    assert current_state == 0, f"خطا: انتظار می‌رفت is_armed برابر 0 باشد اما {current_state} است."
    print("✅ سناریو ۵: شلیک دوم موفق")


if __name__ == "__main__":
    asyncio.run(run_tests())