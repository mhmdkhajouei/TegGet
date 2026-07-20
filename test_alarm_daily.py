import asyncio
import time
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch, ANY

# فرض بر این است که فایل‌های پروژه در مسیرهای زیر قرار دارند
from app.services.alarm_service import evaluate_and_trigger_alarms


class TestDailyAlarmHybridLogic(IsolatedAsyncioTestCase):

    def setUp(self):
        # شبیه‌سازی اپلیکیشن و ربات تلگرام
        self.mock_bot_app = MagicMock()
        self.mock_bot_app.bot.send_message = AsyncMock()

        # زمان حال به صورت یونیکس
        self.now_ts = int(time.time())

    @patch("app.database.get_active_alarms")
    @patch("app.database.is_alarm_triggered_today")
    @patch("app.database.update_alarm_armed_status")
    @patch("app.database.update_alarm_triggered_at")
    async def test_daily_alarm_fires_immediately_when_armed_and_no_previous_trigger_today(
            self, mock_update_trigger, mock_update_armed, mock_is_triggered_today, mock_get_active
    ):
        """سناریو ۱: هشدار روزانه مسلح است، قیمت مرز را می‌شکند و امروز شلیکی ثبت نشده -> باید شلیک کند"""
        mock_get_active.return_value = [{
            "id": 1,
            "chat_id": 12345,
            "target_price": 50000.0,
            "condition": "above",
            "frequency": "daily",
            "last_triggered_at": 0,
            "is_armed": 1
        }]
        mock_is_triggered_today.return_value = False

        await evaluate_and_trigger_alarms(50500.0, "Test Source", self.mock_bot_app)

        self.mock_bot_app.bot.send_message.assert_called_once()
        # استفاده از ANY به جای patch.any
        mock_update_trigger.assert_called_once_with(1, ANY, is_armed=0)

    @patch("app.database.get_active_alarms")
    @patch("app.database.is_alarm_triggered_today")
    @patch("app.database.update_alarm_armed_status")
    @patch("app.database.update_alarm_triggered_at")
    async def test_daily_alarm_postpones_and_disarms_when_already_triggered_today(
            self, mock_update_trigger, mock_update_armed, mock_is_triggered_today, mock_get_active
    ):
        """سناریو ۲: هشدار مسلح است، قیمت مرز را می‌شکند اما امروز قبلاً شلیک داشته -> نباید اسپم کند و باید تفنگ صفر شود"""
        mock_get_active.return_value = [{
            "id": 2,
            "chat_id": 12345,
            "target_price": 50000.0,
            "condition": "above",
            "frequency": "daily",
            "last_triggered_at": self.now_ts,
            "is_armed": 1
        }]
        mock_is_triggered_today.return_value = True

        await evaluate_and_trigger_alarms(50500.0, "Test Source", self.mock_bot_app)

        self.mock_bot_app.bot.send_message.assert_not_called()
        mock_update_armed.assert_called_once_with(2, 0)

    @patch("app.database.get_active_alarms")
    @patch("app.database.is_alarm_triggered_today")
    @patch("app.database.update_alarm_armed_status")
    async def test_daily_alarm_rearms_immediately_on_exact_boundary_return(
            self, mock_update_armed, mock_is_triggered_today, mock_get_active
    ):
        """سناریو ۳: هشدار غیرمسلح است و قیمت به مرز برمی‌گردد -> باید مسلح شود و در همان تیک مجدد شلیک نکند"""
        mock_get_active.return_value = [{
            "id": 3,
            "chat_id": 12345,
            "target_price": 50000.0,
            "condition": "above",
            "frequency": "daily",
            "last_triggered_at": self.now_ts,
            "is_armed": 0
        }]
        # فرستادن True باعث می‌شود موتور بفهمد امروز سهمیه مصرف شده و بعد از Rearm فوری در همان خط شلیک مجدد نکند.
        mock_is_triggered_today.return_value = True

        await evaluate_and_trigger_alarms(50000.0, "Test Source", self.mock_bot_app)

        # اکنون دقیقاً یک‌بار برای مسلح‌سازی صدا زده می‌شود
        mock_update_armed.assert_called_once_with(3, 1)
        self.mock_bot_app.bot.send_message.assert_not_called()

    @patch("app.database.get_active_alarms")
    @patch("app.database.is_alarm_triggered_today")
    @patch("app.database.update_alarm_triggered_at")
    async def test_daily_alarm_executes_postponed_notification_when_day_changes(
            self, mock_update_trigger, mock_is_triggered_today, mock_get_active
    ):
        """سناریو ۴: هشدار غیرمسلح است، قیمت همچنان در شکست مانده اما روز عوض شده -> شلیک معوقه بامداد"""
        mock_get_active.return_value = [{
            "id": 4,
            "chat_id": 12345,
            "target_price": 50000.0,
            "condition": "above",
            "frequency": "daily",
            "last_triggered_at": self.now_ts - 90000,
            "is_armed": 0
        }]
        mock_is_triggered_today.return_value = False

        await evaluate_and_trigger_alarms(50600.0, "Test Source", self.mock_bot_app)

        self.mock_bot_app.bot.send_message.assert_called_once()
        # استفاده از ANY به جای patch.any
        mock_update_trigger.assert_called_once_with(4, ANY, is_armed=0)


if __name__ == "__main__":
    import unittest

    unittest.main()