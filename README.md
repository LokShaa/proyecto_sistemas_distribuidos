# Proyecto Sistemas Distribuidos - Transacciones y Auditoría

Arquitectura distribuida compuesta por un API Gateway (Node.js), Servicio de Dominio (Python/PostgreSQL) y Servicio de Auditoría (Go/MongoDB).

## Requisitos Previos
* Docker y Docker Compose instalados.
* URI de conexión a clúster de MongoDB.

## Instrucciones de Despliegue
1. Clonar el repositorio:
   ```bash
   git clone https://github.com/LokShaa/proyecto_sistemas_distribuidos.git
   cd proyecto_sistemas_distribuidos
```
2. Crear un archivo `.env` en la raíz (basado en un `.env.example` si aplica) o definir la variable temporalmente en la terminal.
3. Ejecutar `export MONGO_URI="tu_cadena_aqui"` (en Linux/Mac) o establecer la variable de entorno en Windows.
4. Levantar los servicios:
   ```bash
   docker compose up --build -d

```
## Pruebas

En la raíz del proyecto se incluye el archivo `pruebas_postman.json`.

1. Abre Postman o Insomnia.
2. Importa el archivo JSON.
3. Ejecuta los escenarios de prueba incluidos (Transacción Exitosa y Transacción Fallida).

## Evidencia del flujo de información

Para verificar que la informacion viajo en el orden requerido se debe realizar los siguientes comandos en la terminal

1. Recepción en el Gateway (Módulo A)
   docker compose logs modulo-a

2. Procesamiento y persistencia en PostgreSQL (Módulo B)
   docker compose logs modulo-b

3. Registro de auditoría en MongoDB (Módulo C)
   docker compose logs modulo-c

