import unittest

from validation import (
    ChannelsConfigError,
    load_channels_config,
    parse_time_list,
    validate_report_frequency,
    validate_time_string,
)


class ValidationTests(unittest.TestCase):
    def test_parse_time_list_normalizes_values(self):
        self.assertEqual(parse_time_list("3:5, 14:09"), ["03:05", "14:09"])

    def test_parse_time_list_requires_at_least_one_entry(self):
        with self.assertRaisesRegex(ValueError, "must contain at least one valid time"):
            parse_time_list(" , ")

    def test_validate_time_string_rejects_out_of_range_values(self):
        with self.assertRaisesRegex(ValueError, "minute must be at most 59"):
            validate_time_string("12:99", "STATUS_REPORT_TIME")

    def test_validate_report_frequency_normalizes_case(self):
        self.assertEqual(validate_report_frequency("BoTh"), "both")

    def test_load_channels_config_reports_line_and_column(self):
        content = (
            "channels:\n"
            "  - id: 123\n"
            "    exclude: falsey\n"
        )

        with self.assertRaisesRegex(
            ChannelsConfigError,
            r"channels\[1\]\.exclude must be true or false at line 3, column 14",
        ):
            load_channels_config(content)

    def test_load_channels_config_accepts_valid_entries(self):
        content = (
            "channels:\n"
            "  - id: 123\n"
            "    name: logs\n"
            "    days: 7\n"
            "    exclude: false\n"
            "    report_individual: true\n"
            "    report_group: Audit Channels\n"
            "    notification_group: Build Channels\n"
            "  - id: 456\n"
            "    type: category\n"
            "    deep_clean: true\n"
            "    report_exclude: false\n"
        )

        channels = load_channels_config(content)

        self.assertEqual(channels[0]["id"], 123)
        self.assertFalse(channels[0]["exclude"])
        self.assertTrue(channels[0]["report_individual"])
        self.assertEqual(channels[0]["report_group"], "Audit Channels")
        self.assertEqual(channels[0]["notification_group"], "Build Channels")
        self.assertEqual(channels[1]["type"], "category")
        self.assertTrue(channels[1]["deep_clean"])
        self.assertFalse(channels[1]["report_exclude"])

    def test_load_channels_config_rejects_non_string_notification_group(self):
        content = (
            "channels:\n"
            "  - id: 123\n"
            "    notification_group: true\n"
        )

        with self.assertRaisesRegex(
            ChannelsConfigError,
            r"channels\[1\]\.notification_group must be a string at line 3, column 25",
        ):
            load_channels_config(content)

    def test_load_channels_config_rejects_non_bool_report_control(self):
        content = (
            "channels:\n"
            "  - id: 123\n"
            "    report_exclude: maybe\n"
        )

        with self.assertRaisesRegex(
            ChannelsConfigError,
            r"channels\[1\]\.report_exclude must be true or false at line 3, column 21",
        ):
            load_channels_config(content)


if __name__ == "__main__":
    unittest.main()
