import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

TOKEN_WHATSAPP = os.getenv("WEBHOOK_TOKEN_WHATSAPP", "")
TOKEN_IFOOD = os.getenv("WEBHOOK_TOKEN_IFOOD", "")
TOKEN_99FOOD = os.getenv("WEBHOOK_TOKEN_99FOOD", "")

EVOLUTION_BASE_URL = os.getenv("EVOLUTION_BASE_URL", "").rstrip("/")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "")
BOT_AUTO_REPLY = os.getenv("BOT_AUTO_REPLY", "true").strip().lower() in {"1", "true", "yes", "on"}

STATUS_MAP = {
    "novo": "recebido",
    "new": "recebido",
    "received": "recebido",
    "recebido": "recebido",
    "accepted": "preparo",
    "accepted_by_store": "preparo",
    "preparing": "preparo",
    "preparo": "preparo",
    "ready_for_delivery": "saiu",
    "out_for_delivery": "saiu",
    "on_route": "saiu",
    "saiu": "saiu",
    "delivered": "entregue",
    "entregue": "entregue",
}


class WebhookResult(BaseModel):
    ok: bool
    canal: str
    action: str
    message: str
    pedido_id: Optional[int] = None


app = FastAPI(title="Mana Control Webhooks", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _must_configured() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(
            status_code=500,
            detail="Configure SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY no .env",
        )


def _channel_token(canal: str) -> str:
    if canal == "wpp":
        return TOKEN_WHATSAPP
    if canal == "ifood":
        return TOKEN_IFOOD
    if canal == "99food":
        return TOKEN_99FOOD
    return ""


def _validate_token(canal: str, received_token: Optional[str]) -> None:
    expected = _channel_token(canal)
    if not expected:
        return
    if received_token != expected:
        raise HTTPException(status_code=401, detail=f"Token inválido para canal {canal}")


def _now_strings() -> Dict[str, str]:
    now = datetime.now()
    return {
        "hora": now.strftime("%H:%M"),
        "data": now.strftime("%Y-%m-%d"),
    }


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip().replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
        try:
            return float(raw)
        except ValueError:
            return default
    return default


def _normalize_payment(value: Any) -> str:
    txt = str(value or "").strip().lower()
    if "pix" in txt:
        return "pix"
    if "dinh" in txt or "cash" in txt:
        return "dinheiro"
    if "cart" in txt or "cred" in txt or "debi" in txt:
        return "cartao"
    return "pix"


def _normalize_status(value: Any) -> str:
    txt = str(value or "").strip().lower()
    return STATUS_MAP.get(txt, "recebido")


def _get(data: Dict[str, Any], *paths: str) -> Any:
    for path in paths:
        ref: Any = data
        valid = True
        for key in path.split("."):
            if isinstance(ref, dict) and key in ref:
                ref = ref[key]
            else:
                valid = False
                break
        if valid:
            return ref
    return None


def _extract_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_items = _get(data, "itens", "items", "order.items", "pedido.itens", "pedido.items")
    if not isinstance(raw_items, list):
        return []
    itens: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("n") or item.get("nome") or item.get("name") or "Item").strip()
        qtd = int(_to_float(item.get("q") or item.get("qtd") or item.get("quantity") or 1, 1))
        preco = _to_float(item.get("p") or item.get("preco") or item.get("price") or 0, 0)
        itens.append({"n": nome, "q": max(qtd, 1), "p": max(preco, 0.0)})
    return itens


def _normalize_jid_to_number(jid: str) -> str:
    if not jid:
        return ""
    base = jid.split("@")[0]
    digits = "".join(ch for ch in base if ch.isdigit())
    return digits


def _extract_whatsapp_text(payload: Any) -> Dict[str, Any]:
    """
    Extrai texto e remetente de payloads comuns do MESSAGES_UPSERT.
    Retorna dict com:
      text, remote_jid, from_me, from_group
    """
    if not isinstance(payload, dict):
        return {
            "text": "",
            "remote_jid": "",
            "from_me": False,
            "from_group": False,
        }

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    key = _get(data, "key") if isinstance(_get(data, "key"), dict) else {}
    message = _get(data, "message") if isinstance(_get(data, "message"), dict) else {}

    remote_jid = (
        _get(data, "key.remoteJid")
        or _get(payload, "data.key.remoteJid")
        or _get(payload, "remoteJid")
        or ""
    )
    from_me = bool(
        _get(data, "key.fromMe")
        or _get(payload, "data.key.fromMe")
        or False
    )
    from_group = bool("-" in str(remote_jid))

    text = (
        _get(data, "message.conversation")
        or _get(data, "message.extendedTextMessage.text")
        or _get(data, "message.imageMessage.caption")
        or _get(data, "message.videoMessage.caption")
        or _get(payload, "text")
        or _get(payload, "message")
        or ""
    )
    if not isinstance(text, str):
        text = str(text or "")
    text = text.strip()

    # Alguns payloads trazem lista de mensagens
    if not text:
        msgs = _get(payload, "data.messages")
        if isinstance(msgs, list) and msgs:
            first = msgs[0] if isinstance(msgs[0], dict) else {}
            text = (
                _get(first, "message.conversation")
                or _get(first, "message.extendedTextMessage.text")
                or _get(first, "message.imageMessage.caption")
                or ""
            )
            if not isinstance(text, str):
                text = str(text or "")
            text = text.strip()
            remote_jid = remote_jid or _get(first, "key.remoteJid") or ""
            from_me = from_me or bool(_get(first, "key.fromMe") or False)
            from_group = bool("-" in str(remote_jid))

    return {
        "text": text,
        "remote_jid": str(remote_jid or ""),
        "from_me": from_me,
        "from_group": from_group,
    }


def _build_auto_reply(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return ""
    if any(k in t for k in ["oi", "olá", "ola", "bom dia", "boa tarde", "boa noite"]):
        return (
            "Paz! Seja bem-vindo ao Maná do Céu. 🍱\n"
            "Posso te ajudar com:\n"
            "1) Cardápio do dia\n"
            "2) Fazer pedido\n"
            "3) Taxa/tempo de entrega\n\n"
            "Responda com 1, 2 ou 3."
        )
    if t in {"1", "cardapio", "cardápio", "menu"}:
        return (
            "Cardápio base:\n"
            "- Marmitex 1 carne: R$ 17,00\n"
            "- Marmitex 2 carnes: R$ 20,00\n"
            "- Econômica: R$ 15,00\n\n"
            "Se quiser pedir, digite: *quero pedir*"
        )
    if t in {"2", "quero pedir", "pedido"}:
        return (
            "Perfeito! Me envie neste formato:\n"
            "Nome + Endereço + Bairro + Itens\n\n"
            "Exemplo:\n"
            "Maria, Rua X 123, Setor Sul, 2 marmitex 1 carne."
        )
    if t in {"3", "taxa", "entrega", "frete"}:
        return "A taxa depende do bairro. Me informe seu bairro que eu te retorno taxa e tempo. 🚚"
    return (
        "Recebi sua mensagem. 🙌\n"
        "Digite *menu* para ver opções ou *quero pedir* para começar o pedido."
    )


async def _send_evolution_text(number: str, text: str) -> bool:
    if not EVOLUTION_BASE_URL or not EVOLUTION_API_KEY or not EVOLUTION_INSTANCE:
        return False
    if not number or not text:
        return False
    url = f"{EVOLUTION_BASE_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {"number": number, "text": text}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, headers=headers, json=payload)
    return resp.status_code < 300


async def _maybe_auto_reply_whatsapp(payload: Any) -> bool:
    if not BOT_AUTO_REPLY:
        return False
    msg = _extract_whatsapp_text(payload)
    if msg["from_me"] or msg["from_group"]:
        return False
    text = msg["text"]
    remote_jid = msg["remote_jid"]
    if not text or not remote_jid:
        return False
    reply = _build_auto_reply(text)
    if not reply:
        return False
    number = _normalize_jid_to_number(remote_jid)
    if not number:
        return False
    return await _send_evolution_text(number, reply)


def _build_pedido_row(payload: Dict[str, Any], canal: str) -> Dict[str, Any]:
    now = _now_strings()

    nome = str(
        _get(
            payload,
            "nome",
            "customer.name",
            "cliente.nome",
            "buyer.name",
            "customerName",
        )
        or "Cliente"
    ).strip()
    tel = str(
        _get(
            payload,
            "tel",
            "telefone",
            "phone",
            "customer.phone",
            "cliente.tel",
            "customerPhone",
        )
        or ""
    ).strip()
    end_rua = str(
        _get(
            payload,
            "end",
            "end_rua",
            "address.street",
            "delivery.address.street",
            "cliente.end",
        )
        or ""
    ).strip()
    bairro = str(
        _get(
            payload,
            "bairro",
            "address.neighborhood",
            "delivery.address.neighborhood",
            "cliente.bairro",
        )
        or ""
    ).strip()
    obs = str(_get(payload, "obs", "notes", "observation", "delivery.observation") or "").strip()

    itens = _extract_items(payload)
    frete = _to_float(_get(payload, "frete", "delivery_fee", "deliveryFee", "totals.delivery"), 0)
    total = _to_float(_get(payload, "total", "totals.total"), 0)
    if not itens and total > 0:
        itens = [{"n": "Pedido", "q": 1, "p": max(total - frete, 0.0)}]

    num_raw = _get(payload, "num", "order_number", "orderId", "order.id", "pedido.num")
    num = str(num_raw).strip() if num_raw is not None else ""
    if not num:
        num = f"#WEB-{int(datetime.now().timestamp())}"
    if not num.startswith("#"):
        num = f"#{num}"

    return {
        "num": num,
        "nome": nome or "Cliente",
        "tel": tel,
        "end_rua": end_rua,
        "bairro": bairro,
        "itens": itens,
        "frete": frete,
        "pag": _normalize_payment(_get(payload, "pag", "payment_method", "payment.method")),
        "status": _normalize_status(_get(payload, "status", "order.status", "event.status")),
        "hora": now["hora"],
        "obs": obs,
        "canal": canal,
        "data": _get(payload, "data", "date", "order.date") or now["data"],
    }


async def _supabase_insert_pedido(row: Dict[str, Any]) -> Optional[int]:
    url = f"{SUPABASE_URL}/rest/v1/pedidos"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    async with httpx.AsyncClient(timeout=25) as client:
        resp = await client.post(url, headers=headers, json=row)
    if resp.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"Supabase pedidos erro: {resp.text}")
    try:
        data = resp.json()
    except ValueError:
        # Alguns ambientes podem retornar 201 sem body mesmo com return=representation.
        return None
    if isinstance(data, list) and data:
        return data[0].get("id")
    return None


async def _supabase_upsert_cliente_from_pedido(row: Dict[str, Any]) -> None:
    tel = (row.get("tel") or "").strip()
    if not tel:
        return
    payload = {
        "nome": row.get("nome") or "Cliente",
        "tel": tel,
        "end_rua": row.get("end_rua") or "",
        "bairro": row.get("bairro") or "",
        "pedidos": 1,
    }
    url = f"{SUPABASE_URL}/rest/v1/clientes?on_conflict=tel"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    async with httpx.AsyncClient(timeout=25) as client:
        resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"Supabase clientes erro: {resp.text}")


async def _handle_order_webhook(canal: str, payload: Dict[str, Any]) -> WebhookResult:
    _must_configured()
    row = _build_pedido_row(payload, canal)
    if not row["itens"]:
        return WebhookResult(
            ok=True,
            canal=canal,
            action="ignored",
            message="Payload recebido, mas sem itens/total para gerar pedido.",
        )

    pedido_id = await _supabase_insert_pedido(row)
    await _supabase_upsert_cliente_from_pedido(row)
    return WebhookResult(
        ok=True,
        canal=canal,
        action="created",
        message="Pedido salvo no Supabase com sucesso.",
        pedido_id=pedido_id,
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "mana-webhooks",
        "supabase_configured": bool(SUPABASE_URL and SUPABASE_KEY),
        "time": datetime.now().isoformat(),
    }


@app.post("/webhook/whatsapp", response_model=WebhookResult)
async def webhook_whatsapp(
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None),
) -> WebhookResult:
    try:
        _validate_token("wpp", x_webhook_token)
        payload = await request.json()
        if not isinstance(payload, dict):
            return WebhookResult(
                ok=True,
                canal="wpp",
                action="ignored",
                message="Payload whatsapp ignorado por formato inválido.",
            )
        replied = await _maybe_auto_reply_whatsapp(payload)
        order_res = await _handle_order_webhook("wpp", payload)
        if order_res.action == "ignored" and replied:
            return WebhookResult(
                ok=True,
                canal="wpp",
                action="replied",
                message="Mensagem recebida e resposta enviada via Evolution.",
            )
        return order_res
    except HTTPException:
        raise
    except Exception as err:
        # Evita loop de retry no Evolution em caso de payload inesperado.
        return WebhookResult(
            ok=False,
            canal="wpp",
            action="error",
            message=f"Erro interno webhook whatsapp: {err}",
        )


@app.post("/webhook/ifood", response_model=WebhookResult)
async def webhook_ifood(
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None),
) -> WebhookResult:
    try:
        _validate_token("ifood", x_webhook_token)
        payload = await request.json()
        return await _handle_order_webhook("ifood", payload)
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Erro interno webhook ifood: {err}")


@app.post("/webhook/99food", response_model=WebhookResult)
async def webhook_99food(
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None),
) -> WebhookResult:
    try:
        _validate_token("99food", x_webhook_token)
        payload = await request.json()
        return await _handle_order_webhook("99food", payload)
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Erro interno webhook 99food: {err}")
