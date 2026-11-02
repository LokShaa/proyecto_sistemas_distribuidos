-- Durante la inicialización todavía no existe una réplica a la que esperar.
SET synchronous_commit = local;
CREATE TABLE IF NOT EXISTS transacciones (
    id UUID PRIMARY KEY,
    cuenta_origen VARCHAR(50) NOT NULL,
    cuenta_destino VARCHAR(50) NOT NULL,
    monto NUMERIC(12, 2) NOT NULL,
    moneda VARCHAR(10) NOT NULL,
    descripcion VARCHAR(255),
    estado VARCHAR(20) NOT NULL DEFAULT 'RECIBIDO',
    fecha TIMESTAMP NOT NULL
);
