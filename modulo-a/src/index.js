const path = require('path');
const fastify = require('fastify')({ logger: true });
const fastifyStatic = require('@fastify/static');
const { request } = require('undici');

// Variables de entorno definidas en docker-compose
const MODULO_B_URL = process.env.MODULO_B_URL || 'http://modulo-b:5000';
const MODULO_C_URL = process.env.MODULO_C_URL || 'http://modulo-c:6000';

// Servir la interfaz gráfica estática desde public/
fastify.register(fastifyStatic, {
  root: path.join(__dirname, '..', 'public'),
  prefix: '/'
});

// Endpoint público: Ingesta y orquestación
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

    // 3. Llamar a Módulo C (Auditoría)
    let auditData = null;
    try {
      const { statusCode: statusC, body: bodyC } = await request(`${MODULO_C_URL}/auditoria`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          transaccion_id: String(transactionId),
          cuenta_origen,
          cuenta_destino,
          monto: String(monto),
          moneda,
          descripcion: descripcion || ''
        })
      });
      if (statusC === 201) {
        auditData = await bodyC.json();
      }
    } catch (auditErr) {
      fastify.log.warn('Aviso: fallo al registrar en auditoría: ' + auditErr.message);
    }

    // 4. Respuesta consolidada al cliente
    return reply.status(201).send({
      mensaje: 'Transacción procesada y auditada con éxito',
      transaccion: dataB,
      auditoria: auditData
    });

  } catch (error) {
    fastify.log.error(error);
    return reply.status(500).send({ error: 'Error interno en comunicación de servicios' });
  }
});

// Endpoint de apoyo para que la interfaz consulte el listado de MongoDB
fastify.get('/api/auditorias', async (req, reply) => {
  try {
    const { statusCode, body } = await request(`${MODULO_C_URL}/auditorias`, {
      method: 'GET'
    });
    const data = await body.json();
    return reply.status(statusCode).send(data);
  } catch (err) {
    return reply.status(500).send([]);
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