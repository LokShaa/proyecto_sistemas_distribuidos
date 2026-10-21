FROM golang:1.22-alpine AS builder
WORKDIR /app

COPY . .

RUN go mod tidy && go mod download

RUN CGO_ENABLED=0 GOOS=linux go build -o modulo-c .

FROM alpine:latest
RUN apk --no-cache add ca-certificates tzdata
WORKDIR /root/
COPY --from=builder /app/modulo-c .
EXPOSE 6000
CMD ["./modulo-c"]