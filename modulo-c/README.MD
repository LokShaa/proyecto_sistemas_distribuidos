## Variables de conexión

El contenedor requiere la siguiente variable de entorno, la cual debe ser proporcionada a través del archivo `.env` en la raíz del proyecto:

| Variable | Descripción |
| --- | --- |
| `MONGO_URI` | Cadena de conexión al clúster remoto de MongoDB Atlas. |

## Ejecución individual

Para levantar el módulo de auditoría de forma aislada (asumiendo que la variable `MONGO_URI` ya está inyectada en tu entorno), ejecuta desde la raíz:

```bash
docker compose up --build modulo-c
```

El módulo C quedará disponible dentro de la red interna en http://modulo-c:6000