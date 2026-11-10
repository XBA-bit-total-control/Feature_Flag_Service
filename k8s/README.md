# Локальный Kubernetes

Манифесты рассчитаны на учебный кластер minikube. MongoDB работает внутри
кластера без аутентификации, а PostgreSQL, MongoDB и RabbitMQ используют
по одному экземпляру и тома локального StorageClass.

## Запуск

Из корня репозитория запустите minikube, соберите образ и загрузите его в кластер.

```console
minikube start --driver=docker --cpus=2 --memory=3200
minikube addons enable metrics-server
docker build -t feature-flag-service:local .
minikube image load feature-flag-service:local
kubectl create namespace feature-flags
```

Создайте файл с секретами вне репозитория. Задайте `POSTGRES_PASSWORD`,
`JWT_SECRET_KEY`, `RABBITMQ_DEFAULT_PASS` и `CELERY_BROKER_URL`. Адрес брокера
имеет форму `amqp://feature_flags:<пароль>@rabbitmq:5672//`. Удобно использовать
случайные шестнадцатеричные значения для паролей и ключа. `LLM_API_KEY` можно
добавить при использовании внешнего провайдера.

```console
kubectl -n feature-flags create secret generic feature-flags-secret --from-env-file=/path/to/secrets.env
kubectl -n feature-flags apply -f k8s/config.yaml -f k8s/infrastructure.yaml
kubectl -n feature-flags rollout status statefulset/postgres --timeout=300s
kubectl -n feature-flags rollout status statefulset/mongo --timeout=300s
kubectl -n feature-flags rollout status statefulset/rabbitmq --timeout=300s
kubectl -n feature-flags apply -f k8s/init.yaml
kubectl -n feature-flags wait --for=condition=complete job/feature-flags-init --timeout=180s
kubectl -n feature-flags apply -f k8s/app.yaml
kubectl -n feature-flags rollout status deployment/api --timeout=180s
kubectl -n feature-flags port-forward service/api 8000:8000
```

На `http://127.0.0.1:8000` доступны `/docs`, `/api/health/live`,
`/api/health/ready` и `/api/metrics`. HPA получает загрузку CPU воркеров от
Metrics Server и масштабирует их от 1 до 5 реплик. Для проверки восстановления
можно удалить один Pod API и дождаться, пока Deployment восстановит вторую реплику.

`/api/health/ready` проверяет PostgreSQL и MongoDB, а `/api/health/live`
проверяет только процесс API. Метрика `feature_flags_resolve_requests_total`
считается отдельно на каждой реплике. `feature_flags_events_pending` показывает
необработанные документы в MongoDB, а `feature_flags_llm_fallback_reports`
показывает число сохранённых отчётов с причиной подмены. Эти две метрики
считываются из общих БД, поэтому при сборе со всех реплик их не суммируют.

Для локального доступа по имени включите Ingress-контроллер с классом `nginx`,
примените `k8s/ingress.yaml` и направьте `feature-flags.local` на адрес контроллера.
