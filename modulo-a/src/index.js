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

// Función auxiliar para enviar cualquier evento a Módulo C
async function registrarAuditoria(payloadAuditoria) {
  try {
    const { statusCode, body } = await request(`${MODULO_C_URL}/auditoria`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payloadAuditoria)
    });
    if (statusCode === 201) {
      return await body.json();
    }
  } catch (auditErr) {
    fastify.log.warn('Aviso: fallo al registrar en auditoría: ' + auditErr.message);
  }
  return null;
}

// Capturar JSON malformado enviado a Módulo A y auditarlo en C
fastify.setErrorHandler(async (error, req, reply) => {
  if (error.statusCode === 400 || error.code === 'FST_ERR_CTP_INVALID_JSON_BODY') {
    await registrarAuditoria({
      transaccion_id: 'N/A',
      cuenta_origen: 'N/A',
      cuenta_destino: 'N/A',
      monto: 'N/A',
      moneda: 'N/A',
      descripcion: 'JSON inválido recibido en Gateway',
      estado: 'ERROR_JSON_INVALIDO',
      detalle_error: error.message
    });
    return reply.status(400).send({ error: 'JSON inválido: ' + error.message });
  }
  reply.status(500).send({ error: error.message });
});

// Endpoint público: Ingesta y orquestación
fastify.post('/api/transacciones', async (req, reply) => {
  const { cuenta_origen, cuenta_destino, monto, moneda, descripcion } = req.body || {};

  const origenLimpio = (cuenta_origen && String(cuenta_origen).trim() !== '') ? String(cuenta_origen).trim() : 'N/A';
  const destinoLimpio = (cuenta_destino && String(cuenta_destino).trim() !== '') ? String(cuenta_destino).trim() : 'N/A';
  const montoLimpio = (monto !== undefined && monto !== null && String(monto).trim() !== '') ? String(monto).trim() : 'N/A';
  const monedaLimpia = (moneda && String(moneda).trim() !== '') ? String(moneda).trim() : 'N/A';

  try {
    // 1. Llamada interna a Módulo B (allí se hacen las validaciones y persistencia)
    const { statusCode: statusB, body: bodyB } = await request(`${MODULO_B_URL}/transacciones`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ cuenta_origen, cuenta_destino, monto, moneda, descripcion })
    });

    const dataB = await bodyB.json();

    // 2. Determinar qué ocurrió en B según su respuesta
    let estadoAuditoria = 'EXITOSA';
    let detalleError = '';

    if (statusB !== 201) {
      // Si fue 422 es validación fallida en B; si fue 500/503 es error de B o caída de Postgres
      estadoAuditoria = statusB === 422 ? 'FALLIDA_VALIDACION_B' : `ERROR_MODULO_B_${statusB}`;
      detalleError = typeof dataB.detail === 'string'
        ? dataB.detail
        : JSON.stringify(dataB.detail || dataB);
    }

    const transactionId = dataB.id || dataB.transaccion_id || 'FALLIDA-SIN-ID';

    // 3. Mandar a Módulo C lo que ocurrió en B (éxito o fallo)
    const auditData = await registrarAuditoria({
      transaccion_id: String(transactionId),
      cuenta_origen: origenLimpio,
      cuenta_destino: destinoLimpio,
      monto: montoLimpio,
      moneda: monedaLimpia,
      descripcion: descripcion || 'Sin descripción',
      estado: estadoAuditoria,
      detalle_error: detalleError
    });

    // Si B falló, devolvemos el código de error de B pero habiendo auditado en C
    if (statusB !== 201) {
      return reply.status(statusB).send({
        mensaje: 'Transacción rechazada o fallida en Módulo B (registrada en auditoría)',
        error: dataB,
        auditoria: auditData
      });
    }

    // 4. Respuesta consolidada al cliente cuando es exitosa
    return reply.status(201).send({
      mensaje: 'Transacción procesada y auditada con éxito',
      transaccion: dataB,
      auditoria: auditData
    });

  } catch (error) {
    // 5. Si el contenedor B está caído, A se lo informa a C
    fastify.log.error(error);
    const auditData = await registrarAuditoria({
      transaccion_id: 'ERROR-RED',
      cuenta_origen: origenLimpio,
      cuenta_destino: destinoLimpio,
      monto: montoLimpio,
      moneda: monedaLimpia,
      descripcion: descripcion || 'Sin descripción',
      estado: 'CAIDA_CONTENEDOR_B',
      detalle_error: 'No se pudo conectar con Módulo B: ' + error.message
    });

    return reply.status(503).send({
      error: 'Módulo B no disponible (evento registrado en auditoría)',
      auditoria: auditData
    });
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

// Endpoint de apoyo para que la interfaz compare BD 1 (bd-relacional) y BD Backup (bd-relacional-backup)
fastify.get('/api/transacciones-bds', async (req, reply) => {
  try {
    const { statusCode, body } = await request(`${MODULO_B_URL}/transacciones-bds`, {
      method: 'GET'
    });
    const data = await body.json();
    return reply.status(statusCode).send(data);
  } catch (err) {
    return reply.status(503).send({
      sincronizadas: false,
      ids_en_ambas: [],
      bd_1: { host: 'bd-relacional', online: false, rol: 'MÓDULO B SIN CONEXIÓN', cantidad: 0, transacciones: [] },
      bd_backup: { host: 'bd-relacional-backup', online: false, rol: 'MÓDULO B SIN CONEXIÓN', cantidad: 0, transacciones: [] }
    });
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
