from utils.hashing import resolve_bucket


class TestResolveBucket:
    """Тесты для функции resolve_bucket."""

    def test_equal_weights(self):
        """Равные веса дают равномерное распределение."""
        weights = [1.0, 1.0, 1.0]
        for bucket in range(100):
            result = resolve_bucket(bucket, weights)
            assert 0 <= result <= 2, (
                f"Bucket {bucket} дал индекс {result} вне диапазона"
            )

    def test_single_variant(self):
        """Один вариант всегда выбирается."""
        weights = [100.0]
        for bucket in range(100):
            result = resolve_bucket(bucket, weights)
            assert result == 0, (
                f"Один вариант должен всегда возвращать индекс 0, "
                f"но получил {result} для bucket {bucket}"
            )

    def test_90_10_split(self):
        """Распределение 90/10."""
        weights = [90.0, 10.0]
        first_variant_count = 0
        for bucket in range(100):
            result = resolve_bucket(bucket, weights)
            if result == 0:
                first_variant_count += 1
        assert first_variant_count == 90, (
            f"Первый вариант должен выбрать 90 bucket, "
            f"но получил {first_variant_count}"
        )

    def test_50_30_20_split(self):
        """Распределение 50/30/20."""
        weights = [50.0, 30.0, 20.0]
        count = [0, 0, 0]
        for bucket in range(100):
            result = resolve_bucket(bucket, weights)
            count[result] += 1
        assert count[0] == 50, (
            f"Первый вариант: ожидалось 50, получено {count[0]}"
        )
        assert count[1] == 30, (
            f"Второй вариант: ожидалось 30, получено {count[1]}"
        )
        assert count[2] == 20, (
            f"Третий вариант: ожидалось 20, получено {count[2]}"
        )

    def test_bucket_0_selects_first(self):
        """Bucket 0 всегда выбирает первый вариант."""
        weights = [1.0, 2.0, 3.0]
        result = resolve_bucket(0, weights)
        assert result == 0, (
            f"Bucket 0 должен выбрать первый вариант, "
            f"но получил индекс {result}"
        )

    def test_bucket_99_selects_last(self):
        """Bucket 99 всегда выбирает последний вариант."""
        weights = [1.0, 2.0, 3.0]
        result = resolve_bucket(99, weights)
        assert result == 2, (
            f"Bucket 99 должен выбрать последний вариант, "
            f"но получил индекс {result}"
        )

    def test_very_small_weights(self):
        """Очень маленькие веса обрабатываются корректно."""
        weights = [0.001, 0.002, 0.003]
        for bucket in range(100):
            result = resolve_bucket(bucket, weights)
            assert 0 <= result <= 2, (
                f"Bucket {bucket} дал индекс {result} вне диапазона"
            )

    def test_very_large_weights(self):
        """Очень большие веса обрабатываются корректно."""
        weights = [1000000.0, 2000000.0, 3000000.0]
        for bucket in range(100):
            result = resolve_bucket(bucket, weights)
            assert 0 <= result <= 2, (
                f"Bucket {bucket} дал индекс {result} вне диапазона"
            )

    def test_zero_weight_first_variant(self):
        """Нулевой вес первого варианта."""
        weights = [0.0, 50.0, 50.0]
        count = [0, 0, 0]
        for bucket in range(100):
            result = resolve_bucket(bucket, weights)
            count[result] += 1
        assert count[0] == 0, (
            f"Первый вариант с нулевым весом должен выбрать 0 bucket, "
            f"но получил {count[0]}"
        )

    def test_zero_weight_middle_variant(self):
        """Нулевой вес среднего варианта."""
        weights = [30.0, 0.0, 70.0]
        count = [0, 0, 0]
        for bucket in range(100):
            result = resolve_bucket(bucket, weights)
            count[result] += 1
        assert count[1] == 0, (
            f"Средний вариант с нулевым весом должен выбрать 0 bucket, "
            f"но получил {count[1]}"
        )

    def test_single_variant_with_zero_weight(self):
        """Единственный вариант с нулевым весом."""
        weights = [0.0]
        result = resolve_bucket(0, weights)
        assert result == 0, (
            "Единственный вариант должен быть выбран даже с нулевым весом"
        )

    def test_two_variants_exact_boundary(self):
        """Два варианта с точной границей."""
        weights = [70.0, 30.0]
        count = [0, 0]
        for bucket in range(100):
            result = resolve_bucket(bucket, weights)
            count[result] += 1
        assert count[0] == 70, (
            f"Первый вариант: ожидалось 70, получено {count[0]}"
        )
        assert count[1] == 30, (
            f"Второй вариант: ожидалось 30, получено {count[1]}"
        )

    def test_fractional_weights(self):
        """Дробные веса обрабатываются корректно."""
        weights = [33.33, 33.33, 33.34]
        for bucket in range(100):
            result = resolve_bucket(bucket, weights)
            assert 0 <= result <= 2, (
                f"Bucket {bucket} дал индекс {result} вне диапазона"
            )

    def test_empty_weights_list(self):
        """Пустой список весов возвращает -1."""
        weights: list[float] = []
        result = resolve_bucket(0, weights)
        assert result == -1, (
            "Пустой список весов должен вернуть -1"
        )
