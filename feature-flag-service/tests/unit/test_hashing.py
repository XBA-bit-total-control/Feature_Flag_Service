from utils.hashing import deterministic_hash


class TestDeterministicHash:
    """Тесты для функции deterministic_hash."""

    def test_returns_value_in_range(self):
        """Хеш всегда возвращает значение от 0 до 99."""
        for i in range(1000):
            result = deterministic_hash(f"user_{i}", "flag", "salt")
            assert 0 <= result <= 99, (
                f"Хеш {result} вне диапазона 0-99 для user_{i}"
            )

    def test_deterministic_same_input(self):
        """Одинаковые входные данные дают одинаковый хеш."""
        result1 = deterministic_hash("user_1", "feature_flag", "salt1")
        result2 = deterministic_hash("user_1", "feature_flag", "salt1")
        assert result1 == result2, (
            "Одинаковые входные данные дают разные хеши"
        )

    def test_different_user_id_different_hash(self):
        """Разные user_id дают разные хеши."""
        hash1 = deterministic_hash("user_1", "flag", "salt")
        hash2 = deterministic_hash("user_2", "flag", "salt")
        assert hash1 != hash2, (
            "Разные user_id дают одинаковый хеш"
        )

    def test_different_flag_key_different_hash(self):
        """Разные flag_key дают разные хеши."""
        hash1 = deterministic_hash("user_1", "flag_a", "salt")
        hash2 = deterministic_hash("user_1", "flag_b", "salt")
        assert hash1 != hash2, (
            "Разные flag_key дают одинаковый хеш"
        )

    def test_different_salt_different_hash(self):
        """Разные salt дают разные хеши."""
        hash1 = deterministic_hash("user_1", "flag", "salt_a")
        hash2 = deterministic_hash("user_1", "flag", "salt_b")
        assert hash1 != hash2, (
            "Разные salt дают одинаковый хеш"
        )

    def test_empty_user_id(self):
        """Пустая строка user_id не вызывает ошибку."""
        result = deterministic_hash("", "flag", "salt")
        assert 0 <= result <= 99, (
            "Пустой user_id дал хеш вне диапазона"
        )

    def test_special_characters_in_user_id(self):
        """Специальные символы обрабатываются корректно."""
        result = deterministic_hash(
            "user@domain.com", "flag:with:colons", "salt"
        )
        assert 0 <= result <= 99, (
            "Спецсимволы дали хеш вне диапазона"
        )

    def test_unicode_characters_in_user_id(self):
        """Unicode символы обрабатываются корректно."""
        result = deterministic_hash(
            "пользователь", "флаг", "соль"
        )
        assert 0 <= result <= 99, (
            "Unicode символы дали хеш вне диапазона"
        )

    def test_long_user_id(self):
        """Длинные user_id обрабатываются корректно."""
        long_id = "a" * 10000
        result = deterministic_hash(long_id, "flag", "salt")
        assert 0 <= result <= 99, (
            "Длинный user_id дал хеш вне диапазона"
        )

    def test_all_bucket_values_covered(self):
        """Проверить что все значения от 0 до 99 достижимы."""
        found = set()
        for i in range(10000):
            found.add(deterministic_hash(
                f"user_{i}", "flag", "salt"
            ))
        assert found == set(range(100)), (
            f"Не все значения диапазона найдены. "
            f"Отсутствуют: {set(range(100)) - found}"
        )
