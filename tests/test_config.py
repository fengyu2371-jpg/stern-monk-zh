import unittest

from config import ConfigError, Settings, is_allowed_channel


class SettingsTests(unittest.TestCase):
    def test_reads_railway_variables(self) -> None:
        settings = Settings.from_mapping(
            {
                "MONK_TOKEN": "test-token",
                "GUILD_ID": "123",
                "MONK_CHANNEL_ID": "456",
                "AI_ENABLED": "true",
                "AI_CONFESSION_ENABLED": "true",
                "AI_ORACLE_ENABLED": "true",
                "OPENAI_API_KEY": "test-key",
                "OPENAI_MODEL": "gpt-5-nano",
                "AI_DAILY_LIMIT": "1",
                "ORACLE_WEEKLY_LIMIT": "3",
                "AI_MAX_OUTPUT_TOKENS": "180",
                "ORACLE_MAX_OUTPUT_TOKENS": "700",
                "MONK_DB_PATH": "/tmp/monk.db",
            }
        )

        self.assertEqual(settings.guild_id, 123)
        self.assertEqual(settings.monk_channel_id, 456)
        self.assertTrue(settings.ai_available)
        self.assertTrue(settings.confession_ai_available)
        self.assertTrue(settings.oracle_ai_available)
        self.assertEqual(settings.monk_db_path, "/tmp/monk.db")
        self.assertEqual(settings.ai_daily_limit, 1)
        self.assertEqual(settings.oracle_weekly_limit, 3)

    def test_usage_limits_default_to_one_and_three(self) -> None:
        settings = Settings.from_mapping({})

        self.assertEqual(settings.ai_daily_limit, 1)
        self.assertEqual(settings.oracle_weekly_limit, 3)

    def test_rejects_zero_usage_limits(self) -> None:
        with self.assertRaises(ConfigError):
            Settings.from_mapping({"AI_DAILY_LIMIT": "0"})
        with self.assertRaises(ConfigError):
            Settings.from_mapping({"ORACLE_WEEKLY_LIMIT": "0"})

    def test_ai_is_unavailable_without_api_key(self) -> None:
        settings = Settings.from_mapping({"AI_ENABLED": "true"})

        self.assertFalse(settings.ai_available)
        self.assertFalse(settings.confession_ai_available)

    def test_confession_ai_defaults_on_when_master_and_key_exist(self) -> None:
        settings = Settings.from_mapping(
            {"AI_ENABLED": "true", "OPENAI_API_KEY": "test-key"}
        )

        self.assertTrue(settings.confession_ai_available)

    def test_master_switch_disables_confession_ai(self) -> None:
        settings = Settings.from_mapping(
            {
                "AI_ENABLED": "false",
                "AI_CONFESSION_ENABLED": "true",
                "OPENAI_API_KEY": "test-key",
            }
        )

        self.assertFalse(settings.confession_ai_available)

    def test_rejects_invalid_boolean(self) -> None:
        with self.assertRaises(ConfigError):
            Settings.from_mapping({"AI_ENABLED": "maybe"})

    def test_rejects_invalid_channel_id(self) -> None:
        with self.assertRaises(ConfigError):
            Settings.from_mapping({"MONK_CHANNEL_ID": "not-a-number"})

    def test_runtime_requires_channel_id(self) -> None:
        settings = Settings.from_mapping({"MONK_TOKEN": "test-token"})

        with self.assertRaises(ConfigError):
            settings.validate_runtime()


class ChannelRestrictionTests(unittest.TestCase):
    def test_allows_configured_channel(self) -> None:
        self.assertTrue(is_allowed_channel(456, 456))

    def test_rejects_other_channel(self) -> None:
        self.assertFalse(is_allowed_channel(999, 456))

    def test_rejects_when_channel_is_not_configured(self) -> None:
        self.assertFalse(is_allowed_channel(456, None))


if __name__ == "__main__":
    unittest.main()
