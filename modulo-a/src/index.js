const fastify = require('fastify')({ logger: true });
const { request } = require('undici');

// Variables de entorno definidas en docker-compose
const MODULO_B_URL = process.env.MODULO_B_URL || 'http://modulo-b:5000';
const MODULO_C_URL = process.env.MODULO_C_URL || 'http://modulo-c:6000';

fastify.post('/api/transacciones', async (req, reply) => {
  const { cuenta_origen, cuenta_destino, monto, moneda, descripcion } = req.body || {};

  // 1. Validación básica de payload requerida por el Gateway
  if (!cuenta_origen || !cuenta_destino || typeof monto !== 'number' || !moneda) {
    return reply.status(400).send({ error: 'Payload inválido o incompleto' });
  }

  try {
    // 2. Llamada interna a Módulo B (Servicio de Dominio)
    const { statusCode: statusB, body: bodyB } = await request(`${MODULO_B_URL}/transacciones`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ cuenta_origen, cuenta_destino, monto, moneda, descripcion })
    });

    const dataB = await bodyB.json();

    if (statusB !== 201) {
      return reply.status(statusB).send(dataB);
    }

    const transactionId = dataB.id || dataB.transaccion_id;

    // 3. Notificación interna a Módulo C (Auditoría)
    try {
      await request(`${MODULO_C_URL}/internal/audit`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          transaccion_id: transactionId,
          evento: 'TRANSACCION_CREADA',
          timestamp: new Date().toISOString(),
          origen: 'API-GATEWAY-NODEJS',
          metadata_red: {
            client_ip: req.ip,
            user_agent: req.headers['user-agent'] || 'desconocido'
          },
          datos_operacion: { cuenta_origen, cuenta_destino, monto, moneda }
        })
      });
    } catch (auditErr) {
      fastify.log.warn('Aviso: fallo al registrar en auditoría: ' + auditErr.message);
    }

    // 4. Respuesta consolidada al cliente
    return reply.status(201).send({
      mensaje: 'Transacción procesada y auditada con éxito',
      transaccion: dataB
    });

  } catch (error) {
    fastify.log.error(error);
    return reply.status(500).send({ error: 'Error interno en comunicación de servicios' });
  }
});

const start = async () => {
  try {
    await fastify.listen({ port: 8080, host: '0.0.0.0' });
  } catch (err) {
    fastify.log.error(err);
    process.exit(1);
  }
};

start();