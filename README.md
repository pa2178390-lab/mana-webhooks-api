# Mana Webhooks API

Backend FastAPI para receber pedidos externos e salvar no Supabase usado pelo seu sistema.

## Endpoints

- `GET /health`
- `POST /webhook/whatsapp`
- `POST /webhook/ifood`
- `POST /webhook/99food`

## 1) Configuração

1. Copie `.env.example` para `.env`.
2. Preencha:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
3. (Opcional) Defina os tokens por canal:
   - `WEBHOOK_TOKEN_WHATSAPP`
   - `WEBHOOK_TOKEN_IFOOD`
   - `WEBHOOK_TOKEN_99FOOD`

Se o token estiver preenchido, o webhook exige header `x-webhook-token`.

## 2) Rodar local

No PowerShell:

```powershell
cd "C:\Users\Lenovo\OneDrive\Documentos\New project\mana_webhooks_api"
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

API local: `http://localhost:8000`
Swagger: `http://localhost:8000/docs`

## 3) Teste rápido

### Health

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/health" -Method Get
```

### WhatsApp

```powershell
$body = @{
  num = "#9001"
  nome = "Maria Teste"
  tel = "62999999999"
  end = "Rua Exemplo, 123"
  bairro = "Parque Tremendao"
  frete = 5
  pag = "pix"
  status = "recebido"
  itens = @(
    @{ n = "Marmitex 1 Carne"; q = 2; p = 17.00 }
  )
} | ConvertTo-Json -Depth 8

Invoke-RestMethod `
  -Uri "http://localhost:8000/webhook/whatsapp" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

### iFood / 99Food

Troque apenas a URL para:
- `http://localhost:8000/webhook/ifood`
- `http://localhost:8000/webhook/99food`

## 4) Expor para internet (webhook real)

Para iFood/99Food/WhatsApp chamarem localmente, exponha sua API com ngrok/cloudflared e configure no provedor:

- `https://SEU-DOMINIO/webhook/whatsapp`
- `https://SEU-DOMINIO/webhook/ifood`
- `https://SEU-DOMINIO/webhook/99food`

## Notas

- O backend grava na tabela `pedidos` do Supabase.
- Também faz upsert básico em `clientes` (por telefone).
- Se chegar payload sem itens e sem total, ele responde `ignored`.
