package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"time"

	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
)

var (
	mongoURI   = getEnv("MONGO_URI", "mongodb://localhost:27017")
	serverPort = getEnv("PORT", "6000")
	dbName     = getEnv("MONGO_DB", "auditoria_db")
	colName    = getEnv("MONGO_COLLECTION", "auditorias")
)

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

type AuditoriaPayload struct {
	TransaccionID string          `json:"transaccion_id"`
	CuentaOrigen  string          `json:"cuenta_origen"`
	CuentaDestino string          `json:"cuenta_destino"`
	Monto         json.Number     `json:"monto"`
	Moneda        string          `json:"moneda"`
	Descripcion   string          `json:"descripcion,omitempty"`
	RawData       json.RawMessage `json:"-"`
}

// RegistroAuditoria es el documento que se persiste en MongoDB.
type RegistroAuditoria struct {
	TransaccionID    string    `bson:"transaccion_id"`
	HashOperacion    string    `bson:"hash_operacion"`    // SHA-256 del payload
	TimestampLlegada time.Time `bson:"timestamp_llegada"` // cuando llegó al módulo C
	IPOrigen         string    `bson:"ip_origen"`         // IP del solicitante (módulo A)
	CuentaOrigen     string    `bson:"cuenta_origen"`
	CuentaDestino    string    `bson:"cuenta_destino"`
	Monto            string    `bson:"monto"`
	Moneda           string    `bson:"moneda"`
	Descripcion      string    `bson:"descripcion,omitempty"`
	Estado           string    `bson:"estado"` // siempre "AUDITADO"
}

var mongoClient *mongo.Client

func conectarMongoDB() {
	log.Printf("[MODULO-C] Conectando a MongoDB Atlas...")

	clientOpts := options.Client().
		ApplyURI(mongoURI).
		SetServerSelectionTimeout(30 * time.Second).
		SetConnectTimeout(30 * time.Second)

	client, err := mongo.Connect(context.Background(), clientOpts)
	if err != nil {
		log.Fatalf("[MODULO-C] Error inicializando cliente MongoDB: %v", err)
	}

	pingCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if err := client.Ping(pingCtx, nil); err != nil {
		log.Fatalf("[MODULO-C] No se pudo hacer ping a MongoDB Atlas: %v", err)
	}

	mongoClient = client
	log.Println("[MODULO-C] Conexión a MongoDB Atlas establecida correctamente")
}

func coleccion() *mongo.Collection {
	return mongoClient.Database(dbName).Collection(colName)
}

func guardarAuditoria(registro RegistroAuditoria) error {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err := coleccion().InsertOne(ctx, registro)
	if err == nil {
		log.Println("[MODULO-C] Evento de auditoría insertado correctamente en MongoDB Atlas.")
	}
	return err
}

func calcularHash(data []byte) string {
	sum := sha256.Sum256(data)
	return fmt.Sprintf("%x", sum)
}

func extraerIP(r *http.Request) string {
	// Intenta leer el header X-Forwarded-For (cuando hay proxy/gateway)
	if ip := r.Header.Get("X-Forwarded-For"); ip != "" {
		return ip
	}
	if ip := r.Header.Get("X-Real-IP"); ip != "" {
		return ip
	}
	return r.RemoteAddr
}

func responderJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(body)
}

func handlerAuditoria(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		responderJSON(w, http.StatusMethodNotAllowed, map[string]string{
			"error": "método no permitido",
		})
		return
	}

	rawBody, err := io.ReadAll(r.Body)
	if err != nil {
		responderJSON(w, http.StatusBadRequest, map[string]string{
			"error": "no se pudo leer el cuerpo de la petición",
		})
		return
	}
	defer r.Body.Close()

	// Deserializar payload
	var payload AuditoriaPayload
	if err := json.Unmarshal(rawBody, &payload); err != nil {
		responderJSON(w, http.StatusBadRequest, map[string]string{
			"error": "JSON inválido: " + err.Error(),
		})
		return
	}

	registro := RegistroAuditoria{
		TransaccionID:    payload.TransaccionID,
		HashOperacion:    calcularHash(rawBody),
		TimestampLlegada: time.Now().UTC(),
		IPOrigen:         extraerIP(r),
		CuentaOrigen:     payload.CuentaOrigen,
		CuentaDestino:    payload.CuentaDestino,
		Monto:            payload.Monto.String(),
		Moneda:           payload.Moneda,
		Descripcion:      payload.Descripcion,
		Estado:           "AUDITADO",
	}

	// Persistir en MongoDB Atlas
	if err := guardarAuditoria(registro); err != nil {
		log.Printf("[MODULO-C] Error guardando auditoría en MongoDB: %v", err)
		responderJSON(w, http.StatusServiceUnavailable, map[string]string{
			"error": "no se pudo guardar la auditoría en MongoDB",
		})
		return
	}

	log.Printf("[MODULO-C] Auditoría guardada | tx_id=%s | hash=%s | ip=%s",
		registro.TransaccionID, registro.HashOperacion, registro.IPOrigen)

	responderJSON(w, http.StatusCreated, map[string]string{
		"status":         "ok",
		"mensaje":        "auditoría registrada correctamente",
		"transaccion_id": registro.TransaccionID,
		"hash":           registro.HashOperacion,
	})
}

// Verificar que el servicio y la conexión a MongoDB estén operativos.
func handlerHealth(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	if err := mongoClient.Ping(ctx, nil); err != nil {
		responderJSON(w, http.StatusServiceUnavailable, map[string]string{
			"status": "error",
			"db":     "MongoDB Atlas no disponible",
		})
		return
	}

	responderJSON(w, http.StatusOK, map[string]string{
		"status":  "ok",
		"servicio": "modulo-c",
		"db":      "MongoDB Atlas conectado",
	})
}

// Devuelve las últimas 20 auditorías guardadas
func handlerListar(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		responderJSON(w, http.StatusMethodNotAllowed, map[string]string{
			"error": "método no permitido",
		})
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	opts := options.Find().
		SetSort(bson.D{{Key: "timestamp_llegada", Value: -1}}).
		SetLimit(20)

	cursor, err := coleccion().Find(ctx, bson.D{}, opts)
	if err != nil {
		responderJSON(w, http.StatusInternalServerError, map[string]string{
			"error": "error consultando MongoDB",
		})
		return
	}
	defer cursor.Close(ctx)

	var registros []RegistroAuditoria
	if err := cursor.All(ctx, &registros); err != nil {
		responderJSON(w, http.StatusInternalServerError, map[string]string{
			"error": "error decodificando documentos",
		})
		return
	}

	responderJSON(w, http.StatusOK, registros)
}

func main() {
	log.Println("[MODULO-C] Iniciando Servicio de Auditoría...")

	// Conectar a MongoDB Atlas
	conectarMongoDB()

	// Registrar rutas
	mux := http.NewServeMux()
	mux.HandleFunc("/auditoria", handlerAuditoria)  // POST - recibe desde Módulo A
	mux.HandleFunc("/auditorias", handlerListar)    // GET  - lista las últimas trazas
	mux.HandleFunc("/health", handlerHealth)        // GET  - healthcheck

	addr := "0.0.0.0:" + serverPort
	log.Printf("[MODULO-C] Escuchando en http://%s", addr)

	server := &http.Server{
		Addr:         addr,
		Handler:      mux,
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 10 * time.Second,
	}

	if err := server.ListenAndServe(); err != nil {
		log.Fatalf("[MODULO-C] Error fatal del servidor: %v", err)
	}
}
