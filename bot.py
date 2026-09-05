import os
import sys
import html
import re
import asyncio
import threading
import time
import hashlib
import base64
from io import BytesIO
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen
import importlib.abc
import importlib.util

base_dir = os.path.dirname(os.path.abspath(__file__))
provider_status = {"consultcenter": "pending"}

# Configure o provedor antes que o modulo compilado importe tconect_api.
os.environ["TCONECT_BASE_URL"] = os.environ.get("TCONECT_BASE_URL", "http://node.tconect.xyz:1116")
os.environ["TCONECT_API_KEY"] = os.environ.get("TCONECT_API_KEY", "DataVip")

# 1. Servidor HTTP leve para manter o Render / UptimeRobot ativo 24/7 sem dormir
def start_health_server():
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.send_header("X-Bot-Commit", os.environ.get("RENDER_GIT_COMMIT", "local")[:7])
            self.send_header("X-ConsultCenter-Status", provider_status["consultcenter"])
            self.end_headers()
            self.wfile.write(b"Bot Telegram Online 24/7")

        def log_message(self, format, *args):
            pass  # Silencia logs de requisições de ping

    try:
        port = int(os.environ.get("PORT", 8080))
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"[Render Healthcheck] Servidor HTTP ativo na porta {port}")
    except Exception as e:
        print(f"[Render Healthcheck] Aviso: {e}")

start_health_server()

def start_keep_alive():
    url = os.environ.get("KEEP_ALIVE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return

    interval = max(1, int(os.environ.get("KEEP_ALIVE_INTERVAL", "300")))

    def ping():
        while True:
            time.sleep(interval)
            try:
                with urlopen(url, timeout=30) as response:
                    response.read(1)
            except Exception as e:
                print(f"[Render Keep-Alive] Aviso: {e}")

    thread = threading.Thread(target=ping, name="render-keep-alive", daemon=True)
    thread.start()
    print(f"[Render Keep-Alive] Ping ativo a cada {interval}s")

start_keep_alive()

# 2. Localizador de bytecode para os módulos compilados
class PycacheFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname in ('bot', 'bot_base'):
            return None
        if path is None or '' in path or base_dir in (path or []):
            pyc_path = os.path.join(base_dir, '__pycache__', f'{fullname}.cpython-314.pyc')
            if os.path.exists(pyc_path):
                return importlib.util.spec_from_file_location(fullname, pyc_path)
        return None

sys.meta_path.insert(0, PycacheFinder())

# 3. Carrega o módulo base compilado
bot_compiled_path = os.path.join(base_dir, '__pycache__/bot_compiled.cpython-314.pyc')
if not os.path.exists(bot_compiled_path):
    bot_compiled_path = os.path.join(base_dir, '__pycache__/bot.cpython-314.pyc')
spec = importlib.util.spec_from_file_location('bot_base', bot_compiled_path)
mod = importlib.util.module_from_spec(spec)
sys.modules['bot_base'] = mod
spec.loader.exec_module(mod)

# O bytecode e carregado de __pycache__, mas os banners ficam na raiz.
mod.BANNER = os.path.join(base_dir, "banner 1.jpg")
mod.BANNER2 = mod.BANNER
mod.BANNER_CONSULTA = os.path.join(base_dir, "BANNER CONSULTA.jpg")

# Salva handlers originais
orig_button_handler = mod.button_handler
orig_post_init = mod.post_init

consultcenter = sys.modules["consultcenter_api"]
consultcenter_retry_lock = asyncio.Lock()

def add_consultcenter_retry(function_name):
    original = getattr(consultcenter, function_name)

    async def with_login_retry(*args, **kwargs):
        result = await original(*args, **kwargs)
        error = str(result.get("erro", "")).lower() if isinstance(result, dict) else ""
        if not any(term in error for term in ("login", "sessao", "autentica")):
            return result

        async with consultcenter_retry_lock:
            await consultcenter.reset_client()
            login_result = await consultcenter.login_consultcenter(force=True)
            if not login_result.get("sucesso"):
                provider_status["consultcenter"] = "error"
                return result

            provider_status["consultcenter"] = "ok"
            return await original(*args, **kwargs)

    setattr(consultcenter, function_name, with_login_retry)
    setattr(mod, function_name, with_login_retry)

for consultcenter_function in (
    "consultar_cpf_consultcenter",
    "consultar_telefone_consultcenter",
    "consultar_cnpj_consultcenter",
    "consultar_nome_consultcenter",
    "consultar_imoveis_cpf_consultcenter",
):
    add_consultcenter_retry(consultcenter_function)

async def custom_post_init(application):
    await orig_post_init(application)
    try:
        login_result = await consultcenter.login_consultcenter(force=True)
        provider_status["consultcenter"] = "ok" if login_result.get("sucesso") else "error"
    except Exception as e:
        provider_status["consultcenter"] = "error"
        mod.logger.warning(f"Falha ao iniciar sessao ConsultCenter: {e}")

# 4. get_delete_markup sem botão de site
def custom_get_delete_markup(user_id, text=None, report_url=None, token=None):
    buttons = []
    # Removido link do site
    if token:
        buttons.append([mod.InlineKeyboardButton("📁 Baixar TXT Resultado", callback_data=f"dltxt_{user_id}_{token}")])
    buttons.append([mod.InlineKeyboardButton("📋 Copiar Dados", callback_data=f"copiar_{user_id}")])
    buttons.append([mod.InlineKeyboardButton("🗑️ Apagar", callback_data=f"apagar_{user_id}")])
    return mod.InlineKeyboardMarkup(buttons)

def extract_photos_from_raw_data(raw_data):
    todas_fotos = []
    if not isinstance(raw_data, dict):
        return todas_fotos

    if raw_data.get("_TODAS_FOTOS_BYTES"):
        lbls = raw_data.get("_TODAS_FOTOS_LABELS", [])
        for idx_f, fb in enumerate(raw_data["_TODAS_FOTOS_BYTES"]):
            lbl = lbls[idx_f] if idx_f < len(lbls) else f"Foto {idx_f+1}"
            todas_fotos.append((fb, lbl))
        return todas_fotos

    if raw_data.get("_FOTO_BYTES"):
        return [(raw_data["_FOTO_BYTES"], "Foto")]

    if raw_data.get("_TODAS_FOTOS_BASE64"):
        lbls = raw_data.get("_TODAS_FOTOS_LABELS", [])
        for idx_f, b64_str in enumerate(raw_data["_TODAS_FOTOS_BASE64"]):
            try:
                b = base64.b64decode(b64_str)
                lbl = lbls[idx_f] if idx_f < len(lbls) else f"Foto {idx_f+1}"
                todas_fotos.append((b, lbl))
            except Exception:
                pass
        if todas_fotos:
            return todas_fotos

    if raw_data.get("_FOTO_BASE64"):
        try:
            return [(base64.b64decode(raw_data["_FOTO_BASE64"]), "Foto")]
        except Exception:
            pass

    for item in raw_data.values():
        if isinstance(item, dict):
            if item.get("_TODAS_FOTOS_BYTES"):
                lbls = item.get("_TODAS_FOTOS_LABELS", [])
                for idx_f, fb in enumerate(item["_TODAS_FOTOS_BYTES"]):
                    lbl = lbls[idx_f] if idx_f < len(lbls) else f"Foto {idx_f+1}"
                    todas_fotos.append((fb, lbl))
                return todas_fotos
            if item.get("_FOTO_BYTES"):
                return [(item["_FOTO_BYTES"], "Foto")]
            if item.get("_TODAS_FOTOS_BASE64"):
                lbls = item.get("_TODAS_FOTOS_LABELS", [])
                for idx_f, b64_str in enumerate(item["_TODAS_FOTOS_BASE64"]):
                    try:
                        b = base64.b64decode(b64_str)
                        lbl = lbls[idx_f] if idx_f < len(lbls) else f"Foto {idx_f+1}"
                        todas_fotos.append((b, lbl))
                    except Exception:
                        pass
                if todas_fotos:
                    return todas_fotos
            if item.get("_FOTO_BASE64"):
                try:
                    return [(base64.b64decode(item["_FOTO_BASE64"]), "Foto")]
                except Exception:
                    pass

    return todas_fotos

# 5. send_result_with_txt enviando direto no PV e com botão de abrir no PV no grupo
async def custom_send_result_with_txt(update, text, title, user_id, report_url=None, raw_data=None, query_value=None, context=None):
    if not text:
        text = "📌 Nenhum dado encontrado."

    token = None
    if not report_url:
        data_to_store = raw_data if raw_data is not None else {"resultado": text}
        token, report_url = mod.create_web_report_with_token(title, data_to_store)

    if not token and report_url and "/r/" in report_url:
        token = report_url.split("/r/")[-1]
    if not token:
        token = "token"

    chat_id = update.effective_chat.id if update.effective_chat else user_id
    is_private = (getattr(update.effective_chat, "type", "") == "private") if update.effective_chat else True
    bot_instance = context.bot if context else update.get_bot()

    todas_fotos = extract_photos_from_raw_data(raw_data)

    foto_bytes = todas_fotos[0][0] if todas_fotos else None

    async def _send_to_pv():
        clean_text = str(text).strip().replace("<blockquote>", "").replace("</blockquote>", "")
        markup_pv = custom_get_delete_markup(user_id, clean_text, report_url=None, token=token)

        # Envia fotos no PV
        if len(todas_fotos) == 1:
            try:
                msg_p = await bot_instance.send_photo(
                    chat_id=user_id,
                    photo=BytesIO(todas_fotos[0][0]),
                    caption=mod.wrap_quote("📷 <b>FOTO DA PESSOA CONSULTADA</b>"),
                    parse_mode="HTML"
                )
                mod.auto_delete(msg_p)
            except Exception as e:
                mod.logger.error(f"Erro ao enviar foto no privado: {e}")
        elif len(todas_fotos) > 1:
            try:
                media_group = []
                for idx, (f_bytes, f_lbl) in enumerate(todas_fotos[:10], 1):
                    cap = mod.wrap_quote(f"📷 <b>FOTO {idx}/{len(todas_fotos)} ({html.escape(f_lbl)})</b>") if idx == 1 else ""
                    media_group.append(mod.InputMediaPhoto(media=BytesIO(f_bytes), caption=cap, parse_mode="HTML"))
                msgs = await bot_instance.send_media_group(chat_id=user_id, media=media_group)
                for m in msgs:
                    mod.auto_delete(m)
            except Exception as e:
                mod.logger.error(f"Erro ao enviar album no privado: {e}")

        # Envia texto no PV
        if len(clean_text) <= 3800:
            msg = await bot_instance.send_message(
                chat_id=user_id,
                text=mod.wrap_quote(clean_text),
                parse_mode="HTML",
                reply_markup=markup_pv
            )
            mod.auto_delete(msg)
        else:
            chunks = [clean_text[i:i+3800] for i in range(0, len(clean_text), 3800)]
            for i, chunk in enumerate(chunks):
                rm = markup_pv if i == len(chunks) - 1 else None
                msg = await bot_instance.send_message(
                    chat_id=user_id,
                    text=mod.wrap_quote(chunk),
                    parse_mode="HTML",
                    reply_markup=rm
                )
                mod.auto_delete(msg)

    if is_private:
        await _send_to_pv()
    else:
        # Se estiver em grupo: envia direto no privado do solicitante
        pv_delivered = False
        try:
            await _send_to_pv()
            pv_delivered = True
        except Exception as e:
            mod.logger.warning(f"Não foi possível enviar direto no PV: {e}")

        # Monta recibo para o grupo
        user = update.effective_user
        raw_uname = f"@{user.username}" if user and user.username else getattr(user, "full_name", "")
        uname = str(user_id) if not raw_uname else raw_uname
        masked_val = mod.mask_value(title, query_value) if query_value else ""

        pv_info_line = "📩 <i>(Resultado enviado com sucesso no seu privado)</i>" if pv_delivered else "⚠️ <i>(Inicie o bot no privado para receber o resultado)</i>"

        receipt_text = mod.wrap_quote(
            f"<b>IMPERIAL SEARCH</b>\n\n"
            f"BEM VINDO {uname}\n\n"
            f"🧾 <b>{html.escape(title)}</b>\n\n"
            f"Informado: <code>{html.escape(masked_val)}</code>\n\n"
            f"{pv_info_line}\n"
            f"👁️ <i>(Toque na foto para visualizar)</i>"
        )

        bot_username = (getattr(bot_instance, "username", "") or "imperialsearchconsultasbot").lstrip("@")
        buttons_group = [
            [mod.PrimaryButton("📩 Ver Resultado no PV", url=f"https://t.me/{bot_username}")],
            [mod.SuccessButton("📁 Baixar TXT Resultado", callback_data=f"dltxt_{user_id}_{token}")],
            [mod.DangerButton("🗑️ Apagar", callback_data=f"apagar_{user_id}")]
        ]
        markup_grp = mod.InlineKeyboardMarkup(buttons_group)

        thread_id = getattr(update.message, "message_thread_id", None) if update.message else None
        kwargs_thread = {"message_thread_id": thread_id} if thread_id else {}

        sent_ok = False
        if foto_bytes:
            try:
                msg = await bot_instance.send_photo(
                    chat_id=chat_id,
                    photo=BytesIO(foto_bytes),
                    caption=receipt_text,
                    parse_mode="HTML",
                    reply_markup=markup_grp,
                    has_spoiler=True,
                    **kwargs_thread
                )
                mod.auto_delete(msg, delay=180)
                sent_ok = True
            except Exception as e:
                mod.logger.warning(f"send_photo com foto da pessoa falhou: {e}")

        if not sent_ok:
            banner_res = mod.BANNER2 if os.path.exists(mod.BANNER2) else (mod.BANNER if os.path.exists(mod.BANNER) else None)
            if banner_res:
                try:
                    with open(banner_res, "rb") as photo:
                        msg = await bot_instance.send_photo(
                            chat_id=chat_id,
                            photo=photo,
                            caption=receipt_text,
                            parse_mode="HTML",
                            reply_markup=markup_grp,
                            **kwargs_thread
                        )
                        mod.auto_delete(msg, delay=180)
                        sent_ok = True
                except Exception as e:
                    mod.logger.warning(f"send_photo falhou, tentando send_message: {e}")

        if not sent_ok:
            try:
                msg = await bot_instance.send_message(
                    chat_id=chat_id,
                    text=receipt_text,
                    parse_mode="HTML",
                    reply_markup=markup_grp,
                    **kwargs_thread
                )
                mod.auto_delete(msg, delay=180)
            except Exception as e:
                mod.logger.error(f"Erro ao enviar recibo no grupo: {e}")

# 6. button_handler com bloqueio exclusivo para terceiros e envio seguro no PV
async def custom_button_handler(update, context):
    query = update.callback_query
    data = query.data
    bot_uname = getattr(context.bot, "username", "imperialsearchconsultasbot")

    if data.startswith("openpv_"):
        parts = data.split("_")
        requester_id = int(parts[1])
        token = parts[2] if len(parts) > 2 else ""

        # BLOQUEIO: Só quem fez a busca pode clicar
        if query.from_user.id != requester_id:
            await query.answer(
                "🔒 ACESSO BLOQUEADO!\n\nEste resultado é privado e pertence exclusivamente a quem realizou a consulta.",
                show_alert=True
            )
            return

        import api_server
        report_item = api_server.get_report_db(token) if token else None

        if not report_item:
            await query.answer("⚠️ Relatório não encontrado ou expirado.", show_alert=True)
            return

        raw_data = report_item.get("data")
        title = report_item.get("title", "CONSULTA")
        text_content = mod.format_result(raw_data, title=title, user=query.from_user, max_len=3800)
        clean_text = re.sub(r"</?tg-emoji[^>]*>", "", text_content).replace("<blockquote>", "").replace("</blockquote>", "")
        markup_pv = custom_get_delete_markup(requester_id, clean_text, report_url=None, token=token)

        try:
            # Fotos
            todas_fotos = extract_photos_from_raw_data(raw_data)

            if len(todas_fotos) == 1:
                msg_p = await context.bot.send_photo(
                    chat_id=requester_id,
                    photo=BytesIO(todas_fotos[0][0]),
                    caption=mod.wrap_quote("📷 <b>FOTO DA PESSOA CONSULTADA</b>"),
                    parse_mode="HTML"
                )
                mod.auto_delete(msg_p)
            elif len(todas_fotos) > 1:
                media_group = []
                for idx, (f_bytes, f_lbl) in enumerate(todas_fotos[:10], 1):
                    cap = mod.wrap_quote(f"📷 <b>FOTO {idx}/{len(todas_fotos)} ({html.escape(f_lbl)})</b>") if idx == 1 else ""
                    media_group.append(mod.InputMediaPhoto(media=BytesIO(f_bytes), caption=cap, parse_mode="HTML"))
                msgs = await context.bot.send_media_group(chat_id=requester_id, media=media_group)
                for m in msgs:
                    mod.auto_delete(m)

            # Texto
            if len(clean_text) <= 3800:
                msg = await context.bot.send_message(
                    chat_id=requester_id,
                    text=mod.wrap_quote(clean_text),
                    parse_mode="HTML",
                    reply_markup=markup_pv
                )
                mod.auto_delete(msg)
            else:
                chunks = [clean_text[i:i+3800] for i in range(0, len(clean_text), 3800)]
                for i, chunk in enumerate(chunks):
                    rm = markup_pv if i == len(chunks) - 1 else None
                    msg = await context.bot.send_message(
                        chat_id=requester_id,
                        text=mod.wrap_quote(chunk),
                        parse_mode="HTML",
                        reply_markup=rm
                    )
                    mod.auto_delete(msg)

            await query.answer("✅ Resultado enviado no seu privado com sucesso!", show_alert=True)
        except Exception as e:
            mod.logger.warning(f"Falha ao entregar no privado: {e}")
            await query.answer(
                f"⚠️ Abra uma conversa no privado com @{bot_uname} e clique em /start para receber o resultado!",
                show_alert=True
            )
        return

    return await orig_button_handler(update, context)

import apisbrasilpro_api

# Vincula helpers de apisbrasilpro_api ao mod compilado
mod.consultar_todas_fotos_apisbrasilpro_async = apisbrasilpro_api.consultar_todas_fotos_apisbrasilpro_async
mod.consultar_todas_fotos_apisbrasilpro = apisbrasilpro_api.consultar_todas_fotos_apisbrasilpro
mod.consultar_foto_apisbrasilpro = apisbrasilpro_api.consultar_foto_apisbrasilpro
mod.consultar_foto_apisbrasilpro_async = apisbrasilpro_api.consultar_foto_apisbrasilpro_async
mod.consultar_email_apisbrasilpro = apisbrasilpro_api.consultar_email_apisbrasilpro
mod.consultar_mae_apisbrasilpro = apisbrasilpro_api.consultar_mae_apisbrasilpro
mod.consultar_pai_apisbrasilpro = apisbrasilpro_api.consultar_pai_apisbrasilpro

# 6. Override para busca unificada de fotos com suporte a todas as 10 bases de fotos do ApisBrasilPro + DataVip + SISP + PMSE
async def custom_buscar_todas_fotos_unificada(cpf):
    cpf_clean = re.sub(r'\D', '', str(cpf or ''))
    if not cpf_clean:
        return [], {}
    fotos_list = []
    seen_hashes = set()
    info_merged = {}

    def add_foto(img_bytes, origem):
        if not img_bytes or len(img_bytes) < 200:
            return
        h = hashlib.md5(img_bytes).hexdigest()
        if h in seen_hashes:
            return
        seen_hashes.add(h)
        fotos_list.append((img_bytes, origem))

    async def _fetch_tconect():
        try:
            import tconect_api
            return await asyncio.wait_for(
                asyncio.to_thread(tconect_api.consultar_tconect_foto_nacional, cpf_clean),
                timeout=25.0
            )
        except Exception as e:
            mod.logger.warning(f"Erro ao buscar foto tconect: {e}")
            return None, {}

    async def _fetch_apisbrasil():
        try:
            return await asyncio.wait_for(
                apisbrasilpro_api.consultar_todas_fotos_apisbrasilpro_async(cpf_clean),
                timeout=15.0
            )
        except Exception as e:
            mod.logger.warning(f"Erro ao buscar foto apisbrasil: {e}")
            return []

    async def _fetch_sisp():
        try:
            coro = mod.buscar_foto_sisp(cpf_clean)
            if asyncio.iscoroutine(coro):
                return await asyncio.wait_for(coro, timeout=8.0)
            return coro
        except Exception:
            return None, {}

    async def _fetch_pmse():
        try:
            coro = mod.buscar_foto_pmse(cpf_clean)
            if asyncio.iscoroutine(coro):
                return await asyncio.wait_for(coro, timeout=8.0)
            return coro
        except Exception:
            return None, {}

    tc_res, ap_res, sisp_res, pmse_res = await asyncio.gather(
        _fetch_tconect(),
        _fetch_apisbrasil(),
        _fetch_sisp(),
        _fetch_pmse()
    )

    if tc_res and isinstance(tc_res, tuple) and tc_res[0]:
        img_bytes, info = tc_res
        add_foto(img_bytes, info.get('origem', 'Base Nacional Foto') if isinstance(info, dict) else 'Base Nacional Foto')
        if isinstance(info, dict):
            info_merged.update(info)

    if ap_res and isinstance(ap_res, list):
        for item in ap_res:
            if isinstance(item, tuple) and len(item) == 2:
                img_bytes, info = item
                add_foto(img_bytes, info.get('FONTE', 'ApisBrasilPro') if isinstance(info, dict) else 'ApisBrasilPro')
                if isinstance(info, dict):
                    info_merged.update(info)

    if sisp_res and isinstance(sisp_res, tuple) and sisp_res[0]:
        img_bytes, info = sisp_res
        add_foto(img_bytes, 'SISP Portal')
        if isinstance(info, dict):
            info_merged.update(info)

    if pmse_res and isinstance(pmse_res, tuple) and pmse_res[0]:
        img_bytes, info = pmse_res
        add_foto(img_bytes, 'Portal PMSE')
        if isinstance(info, dict):
            info_merged.update(info)

    return fotos_list, info_merged

async def custom_buscar_foto_unificada(cpf):
    fotos, info = await custom_buscar_todas_fotos_unificada(cpf)
    if fotos:
        return fotos[0][0], info
    return None, info

async def custom_check_access_and_chat(update, context, user_id=None):
    return True

async def custom_cmd_foto(update, context):
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return

    text = (update.message.text or "").strip() if update.message else ""
    parts = text.split(maxsplit=1)
    cpf_arg = ""
    if len(parts) > 1 and parts[0].startswith("/foto"):
        cpf_arg = parts[1].strip()
    elif not text.startswith("/"):
        cpf_arg = text
    elif getattr(context, "args", None):
        cpf_arg = context.args[0].strip()

    cpf = re.sub(r"\D", "", cpf_arg)
    if len(cpf) != 11:
        if not cpf_arg:
            mod.user_states[user_id] = "btn_foto"
            msg = await mod.send_chat_msg(context, update, mod.wrap_quote("Digite o CPF para buscar FOTO:"), parse_mode="HTML")
            mod.auto_delete(msg, delay=60)
        else:
            msg = await mod.send_chat_msg(context, update, mod.wrap_quote("⚠️ CPF inválido. Digite um CPF válido com até 11 dígitos."), parse_mode="HTML")
            mod.auto_delete(msg, delay=15)
        return

    user = update.effective_user
    uname_tag = f"@{user.username}" if user and user.username else getattr(user, "full_name", "") or str(user_id)
    msg_wait = await mod.send_loading_message(
        context,
        update.effective_chat.id,
        uname_tag,
        update=update,
        text=f"🔍 {uname_tag} Buscando foto... aguarde."
    )

    try:
        fotos_list, dados = await asyncio.wait_for(custom_buscar_todas_fotos_unificada(cpf), timeout=35)
        if not fotos_list:
            msg = await mod.send_chat_msg(context, update, mod.wrap_quote("📌 <b>FOTO</b>: nenhuma foto encontrada para este CPF."), parse_mode="HTML")
            mod.auto_delete(msg, delay=15)
            await mod.delete_msg(msg_wait)
            return

        title_f = f"CONSULTA FOTO ({len(fotos_list)} FOTO(S) ENCONTRADA(S))" if len(fotos_list) > 1 else "CONSULTA FOTO"
        texto_info = mod.format_pmse_data(dados) if dados else f"CPF: {cpf}"

        raw_data = {
            "resultado": texto_info,
            "_TODAS_FOTOS_BYTES": [f[0] for f in fotos_list],
            "_TODAS_FOTOS_LABELS": [f[1] for f in fotos_list],
            "_FOTO_BYTES": fotos_list[0][0]
        }
        try:
            raw_data["_FOTO_BASE64"] = base64.b64encode(fotos_list[0][0]).decode("ascii")
            raw_data["_TODAS_FOTOS_BASE64"] = [base64.b64encode(f[0]).decode("ascii") for f in fotos_list]
        except Exception:
            pass

        await custom_send_result_with_txt(
            update, texto_info, title_f, user_id,
            raw_data=raw_data, query_value=cpf, context=context
        )
        await mod.delete_msg(msg_wait)
    except asyncio.TimeoutError:
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote("⏰ Consulta excedeu o tempo limite."), parse_mode="HTML")
        mod.auto_delete(msg, delay=15)
        await mod.delete_msg(msg_wait)
    except Exception as e:
        mod.logger.error(f"Erro no cmd_foto: {e}")
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote(f"❌ Erro ao buscar foto: {mod.sanitize_error_msg(str(e))}"), parse_mode="HTML")
        mod.auto_delete(msg, delay=15)
        await mod.delete_msg(msg_wait)


# 7. Merge completo de CPF integrando TODAS as APIs do ApisBrasilPro
orig_merge_cpf_local = mod.merge_cpf_local

async def custom_merge_cpf_local(cpf):
    cpf_clean = re.sub(r"\D", "", str(cpf or ""))
    if not cpf_clean:
        return {}

    async def _safe(coro, timeout=12.0):
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except Exception:
            return {}

    orig_coro = _safe(orig_merge_cpf_local(cpf_clean), timeout=18.0)
    cred_coro = _safe(apisbrasilpro_api.consultar_credilink_cpf_apisbrasilpro_async(cpf_clean))
    spc1_coro = _safe(apisbrasilpro_api.consultar_spc1_doc_apisbrasilpro_async(cpf_clean))
    tel0_coro = _safe(apisbrasilpro_api.consultar_telefone0_cpf_apisbrasilpro_async(cpf_clean))
    paycom_coro = _safe(apisbrasilpro_api.consultar_paycom_identity_apisbrasilpro_async(cpf_clean))
    claro_coro = _safe(apisbrasilpro_api.consultar_claro_cpf_async(cpf_clean))
    cadsus_coro = _safe(apisbrasilpro_api.consultar_cadsus_cpf_async(cpf_clean))
    nextel_coro = _safe(apisbrasilpro_api.consultar_nextel_cpf_async(cpf_clean))
    rais_coro = _safe(apisbrasilpro_api.consultar_rais2019_cpf_async(cpf_clean))
    br21m_coro = _safe(apisbrasilpro_api.consultar_br21m_doc_async(cpf_clean))
    people_coro = _safe(apisbrasilpro_api.consultar_brazilianpeople_cpf_async(cpf_clean))

    (
        merged_orig,
        res_cred,
        res_spc1,
        res_tel0,
        res_paycom,
        res_claro,
        res_cadsus,
        res_nextel,
        res_rais,
        res_br21m,
        res_people,
    ) = await asyncio.gather(
        orig_coro,
        cred_coro,
        spc1_coro,
        tel0_coro,
        paycom_coro,
        claro_coro,
        cadsus_coro,
        nextel_coro,
        rais_coro,
        br21m_coro,
        people_coro,
    )

    merged = dict(merged_orig) if isinstance(merged_orig, dict) else {}

    # 1. Coleta e unifica telefones
    all_phones = set()
    raw_phones = merged.get("TELEFONES") or merged.get("telefones") or []
    if isinstance(raw_phones, str):
        for p in raw_phones.split(","):
            p_clean = re.sub(r"\D", "", p)
            if len(p_clean) in (10, 11):
                all_phones.add(p_clean)
    elif isinstance(raw_phones, list):
        for p in raw_phones:
            p_clean = re.sub(r"\D", "", str(p))
            if len(p_clean) in (10, 11):
                all_phones.add(p_clean)

    def _extract_phones(d):
        if not isinstance(d, dict):
            return
        for k, v in d.items():
            if any(term in k.lower() for term in ("telefone", "tel", "celular", "fone")):
                if isinstance(v, str):
                    p_clean = re.sub(r"\D", "", v)
                    if p_clean.startswith("55") and len(p_clean) in (12, 13):
                        p_clean = p_clean[2:]
                    if len(p_clean) in (10, 11):
                        all_phones.add(p_clean)
                elif isinstance(v, list):
                    for item_p in v:
                        p_clean = re.sub(r"\D", "", str(item_p))
                        if p_clean.startswith("55") and len(p_clean) in (12, 13):
                            p_clean = p_clean[2:]
                        if len(p_clean) in (10, 11):
                            all_phones.add(p_clean)

    for r in (res_cred, res_spc1, res_tel0, res_claro, res_cadsus, res_nextel, res_br21m):
        if isinstance(r, dict):
            _extract_phones(r)
            for sub_key in ("dados", "data", "results", "resultados"):
                sub = r.get(sub_key)
                if isinstance(sub, list):
                    for item in sub:
                        if isinstance(item, dict):
                            _extract_phones(item)

    if isinstance(res_paycom, dict):
        for item in res_paycom.get("dados", []):
            if isinstance(item, dict) and item.get("telephone"):
                p_clean = re.sub(r"\D", "", str(item["telephone"]))
                if p_clean.startswith("55") and len(p_clean) in (12, 13):
                    p_clean = p_clean[2:]
                if len(p_clean) in (10, 11):
                    all_phones.add(p_clean)

    if all_phones:
        formatted_phones = []
        for p in sorted(all_phones):
            if len(p) == 11:
                formatted_phones.append(f"({p[:2]}) {p[2:7]}-{p[7:]}")
            elif len(p) == 10:
                formatted_phones.append(f"({p[:2]}) {p[2:6]}-{p[6:]}")
        merged["TELEFONES"] = ", ".join(formatted_phones)
        merged["TELEFONES_LISTA"] = formatted_phones

    # 2. Coleta e unifica emails
    all_emails = set()
    raw_emails = merged.get("EMAILS") or merged.get("emails") or []
    if isinstance(raw_emails, str):
        for e in raw_emails.split(","):
            if "@" in e:
                all_emails.add(e.strip().lower())
    elif isinstance(raw_emails, list):
        for e in raw_emails:
            if isinstance(e, str) and "@" in e:
                all_emails.add(e.strip().lower())

    for r in (res_paycom, res_cred, res_cadsus):
        if isinstance(r, dict):
            for sub_key in ("dados", "data", "results"):
                sub = r.get(sub_key)
                if isinstance(sub, list):
                    for item in sub:
                        if isinstance(item, dict):
                            em = str(item.get("email") or item.get("EMAIL") or "").strip().lower()
                            if "@" in em and len(em) > 5:
                                all_emails.add(em)
            em = str(r.get("email") or r.get("EMAIL") or "").strip().lower()
            if "@" in em and len(em) > 5:
                all_emails.add(em)

    if all_emails:
        merged["EMAILS"] = ", ".join(sorted(all_emails))
        merged["EMAILS_LISTA"] = sorted(all_emails)

    # 3. RAIS 2019 (Histórico Profissional / Empregos)
    if isinstance(res_rais, dict) and res_rais.get("data"):
        data_rais = res_rais.get("data")
        if isinstance(data_rais, list):
            jobs = merged.get("HISTORICO_PROFISSIONAL") or []
            if isinstance(jobs, str):
                jobs = [jobs]
            for item in data_rais:
                if isinstance(item, dict):
                    if not merged.get("PIS") and item.get("PIS"):
                        merged["PIS"] = str(item["PIS"]).strip()
                    empresa = item.get("NOM_RAZAO_") or item.get("NOM_FANTAS") or item.get("CNPJ") or "Empresa"
                    salario = item.get("SAL_MENSAL_DECLARADO") or item.get("SALARIO_MENSAL")
                    sal_str = f"R$ {salario}" if salario else ""
                    adm = str(item.get("ADMISSAO") or "")[:10]
                    cbo = item.get("CBO_2002") or ""
                    job_desc = f"🏢 {empresa} | Cargo CBO: {cbo}"
                    if sal_str:
                        job_desc += f" | Salário: {sal_str}"
                    if adm and adm != "None":
                        job_desc += f" | Admissão: {adm}"
                    if job_desc not in jobs:
                        jobs.append(job_desc)
            if jobs:
                merged["HISTORICO_PROFISSIONAL"] = jobs
                merged["EMPREGOS"] = jobs

    # 4. PayCom (Compras)
    if isinstance(res_paycom, dict) and res_paycom.get("dados"):
        compras = merged.get("HISTORICO_COMPRAS") or []
        if isinstance(compras, str):
            compras = [compras]
        for item in res_paycom.get("dados", []):
            if isinstance(item, dict):
                cid = item.get("id") or item.get("buyer_id") or ""
                dt = str(item.get("created_at") or "")[:10]
                em = item.get("email") or ""
                tel = item.get("telephone") or ""
                compras.append(f"🛒 Pedido #{cid} - Data: {dt} (Email: {em}, Tel: {tel})")
        if compras:
            merged["HISTORICO_COMPRAS"] = compras

    # 5. Brazilian People, Cadsus, Credilink (Enriquecimento Cadastral)
    for r in (res_people, res_cadsus, res_cred):
        if isinstance(r, dict):
            items = []
            for sub_key in ("data", "dados", "results"):
                sub = r.get(sub_key)
                if isinstance(sub, list):
                    items.extend(sub)
                elif isinstance(sub, dict):
                    items.append(sub)
            for it in items:
                if isinstance(it, dict):
                    if not merged.get("NOME") and (it.get("nome") or it.get("NOME")):
                        merged["NOME"] = str(it.get("nome") or it.get("NOME")).strip().upper()
                    if not merged.get("DT_NASCIMENTO") and (it.get("nascimento") or it.get("DT_NASCIMENTO")):
                        merged["DT_NASCIMENTO"] = str(it.get("nascimento") or it.get("DT_NASCIMENTO")).strip()
                    if not merged.get("NOME_MAE") and (it.get("mae") or it.get("NOME_MAE")):
                        merged["NOME_MAE"] = str(it.get("mae") or it.get("NOME_MAE")).strip().upper()
                    if not merged.get("NOME_PAI") and (it.get("pai") or it.get("NOME_PAI")):
                        merged["NOME_PAI"] = str(it.get("pai") or it.get("NOME_PAI")).strip().upper()
                    if not merged.get("RG") and (it.get("rg") or it.get("RG")):
                        merged["RG"] = str(it.get("rg") or it.get("RG")).strip()
                    if not merged.get("SEXO") and (it.get("sexo") or it.get("SEXO")):
                        merged["SEXO"] = str(it.get("sexo") or it.get("SEXO")).strip().upper()

    if merged.get("ERRO") and (merged.get("NOME") or merged.get("DT_NASCIMENTO") or merged.get("HISTORICO_PROFISSIONAL") or all_phones):
        merged.pop("ERRO", None)
        merged["CPF"] = cpf_clean

    return merged

# 8. Busca por Nome unificada com todas as bases do ApisBrasilPro
orig_buscar_por_nome = mod.buscar_por_nome

async def custom_buscar_por_nome(nome: str) -> list:
    nome_clean = str(nome or "").strip()
    if not nome_clean:
        return []

    async def _safe(coro):
        try:
            return await asyncio.wait_for(coro, timeout=12.0)
        except Exception:
            return {}

    orig_task = _safe(orig_buscar_por_nome(nome_clean))
    serasa_task = _safe(apisbrasilpro_api.consultar_serasa_nome_apisbrasilpro_async(nome_clean))
    spc2_task = _safe(apisbrasilpro_api.consultar_spc2_nome_apisbrasilpro_async(nome_clean))
    cred_task = _safe(apisbrasilpro_api.consultar_credilink_nome_apisbrasilpro_async(nome_clean))
    tel1_task = _safe(apisbrasilpro_api.consultar_telefone1_nome_apisbrasilpro_async(nome_clean))
    fotoma_task = _safe(apisbrasilpro_api.consultar_fotoma_nome_apisbrasilpro_async(nome_clean))
    fotoro_task = _safe(apisbrasilpro_api.consultar_fotoro_nome_apisbrasilpro_async(nome_clean))
    claro_task = _safe(apisbrasilpro_api.consultar_claro_nome_async(nome_clean))
    cadsus_task = _safe(apisbrasilpro_api.consultar_cadsus_nome_async(nome_clean))

    (
        res_orig,
        res_serasa,
        res_spc2,
        res_cred,
        res_tel1,
        res_fotoma,
        res_fotoro,
        res_claro,
        res_cadsus,
    ) = await asyncio.gather(
        orig_task,
        serasa_task,
        spc2_task,
        cred_task,
        tel1_task,
        fotoma_task,
        fotoro_task,
        claro_task,
        cadsus_task,
    )

    candidatos = list(res_orig) if isinstance(res_orig, list) else []
    seen_cpfs = set()
    for c in candidatos:
        if isinstance(c, dict):
            cpf_val = re.sub(r"\D", "", str(c.get("CPF") or c.get("cpf") or ""))
            if cpf_val:
                seen_cpfs.add(cpf_val)

    def _add_cand(item):
        if not isinstance(item, dict):
            return
        cpf_cand = re.sub(r"\D", "", str(item.get("CPF") or item.get("cpf") or item.get("cpf_cnpj") or item.get("doc") or ""))
        nome_cand = str(item.get("NOME") or item.get("nome") or item.get("name") or "").strip().upper()
        if not nome_cand and not cpf_cand:
            return
        if cpf_cand and cpf_cand in seen_cpfs:
            return
        if cpf_cand:
            seen_cpfs.add(cpf_cand)

        cand = {
            "NOME": nome_cand or "NÃO INFORMADO",
            "CPF": cpf_cand,
            "DT_NASCIMENTO": str(item.get("DT_NASCIMENTO") or item.get("data_nascimento") or item.get("nascimento") or "").strip(),
            "MÃE": str(item.get("NOME_MAE") or item.get("mae") or item.get("mother") or "").strip().upper(),
            "PAI": str(item.get("NOME_PAI") or item.get("pai") or item.get("father") or "").strip().upper(),
            "RG": str(item.get("RG") or item.get("rg") or "").strip(),
            "LOCALIDADE": str(item.get("CIDADE") or item.get("cidade") or item.get("municipio") or item.get("uf") or "").strip().upper()
        }
        candidatos.append(cand)

    for r in (res_serasa, res_spc2, res_cred, res_tel1, res_fotoma, res_fotoro, res_claro, res_cadsus):
        if isinstance(r, dict):
            _add_cand(r)
            for sub_key in ("resultados", "results", "dados", "data"):
                sub = r.get(sub_key)
                if isinstance(sub, list):
                    for it in sub:
                        _add_cand(it)

    return candidatos

# 9. Busca por Telefone unificada com ApisBrasilPro
orig_buscar_por_telefone = mod.buscar_por_telefone

async def custom_buscar_por_telefone(telefone: str) -> list:
    tel_clean = re.sub(r"\D", "", str(telefone or ""))
    if not tel_clean:
        return []

    async def _safe(coro):
        try:
            return await asyncio.wait_for(coro, timeout=12.0)
        except Exception:
            return {}

    orig_task = _safe(orig_buscar_por_telefone(tel_clean))
    pay1_task = _safe(apisbrasilpro_api.consultar_paycom_telephone_apisbrasilpro_async(tel_clean))
    pay2_task = _safe(apisbrasilpro_api.consultar_paycom2_telefone_apisbrasilpro_async(tel_clean))
    claro_task = _safe(apisbrasilpro_api.consultar_claro_telefone_async(tel_clean))
    cadsus_task = _safe(apisbrasilpro_api.consultar_cadsus_celular_async(tel_clean))
    nextel_task = _safe(apisbrasilpro_api.consultar_nextel_telefone_async(tel_clean))
    br21m_task = _safe(apisbrasilpro_api.consultar_br21m_telefone_async(tel_clean))

    res_orig, res_p1, res_p2, res_claro, res_cadsus, res_nextel, res_br21m = await asyncio.gather(
        orig_task, pay1_task, pay2_task, claro_task, cadsus_task, nextel_task, br21m_task
    )

    resultados = list(res_orig) if isinstance(res_orig, list) else []
    seen = set()
    for item in resultados:
        if isinstance(item, dict):
            c = re.sub(r"\D", "", str(item.get("CPF") or item.get("cpf") or item.get("doc") or ""))
            if c:
                seen.add(c)

    def _add_item(d):
        if not isinstance(d, dict):
            return
        cpf = re.sub(r"\D", "", str(d.get("CPF") or d.get("cpf") or d.get("doc") or d.get("identity") or ""))
        nome = str(d.get("NOME") or d.get("nome") or d.get("name") or "").strip().upper()
        if cpf and cpf not in seen:
            seen.add(cpf)
            resultados.append({"CPF": cpf, "NOME": nome or "NÃO INFORMADO", "FONTE": "ApisBrasilPro"})

    for r in (res_p1, res_p2, res_claro, res_cadsus, res_nextel, res_br21m):
        if isinstance(r, dict):
            _add_item(r)
            for k in ("dados", "data", "results", "resultados"):
                sub = r.get(k)
                if isinstance(sub, list):
                    for it in sub:
                        _add_item(it)

    return resultados

# 10. Consulta de RG unificada (Serasa + Dados01)
orig_cmd_rg = mod.cmd_rg

async def custom_cmd_rg(update, context):
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return

    text = (update.message.text or "").strip() if update.message else ""
    parts = text.split(maxsplit=1)
    rg_arg = parts[1].strip() if len(parts) > 1 else ""
    if not rg_arg and getattr(context, "args", None):
        rg_arg = context.args[0].strip()

    rg_clean = re.sub(r"[^A-Za-z0-9]", "", rg_arg)
    if not rg_clean:
        if not rg_arg:
            mod.user_states[user_id] = "btn_rg"
            msg = await mod.send_chat_msg(context, update, mod.wrap_quote("Digite o RG para consulta:"), parse_mode="HTML")
            mod.auto_delete(msg, delay=60)
        else:
            msg = await mod.send_chat_msg(context, update, mod.wrap_quote("⚠️ RG inválido."), parse_mode="HTML")
            mod.auto_delete(msg, delay=15)
        return

    user = update.effective_user
    uname_tag = f"@{user.username}" if user and user.username else getattr(user, "full_name", "") or str(user_id)
    msg_wait = await mod.send_loading_message(context, update.effective_chat.id, uname_tag, update=update, text=f"🔍 {uname_tag} Consultando RG... aguarde.")

    try:
        t_serasa = apisbrasilpro_api.consultar_serasa_rg_apisbrasilpro_async(rg_clean)
        t_dados = apisbrasilpro_api.consultar_dados01_rg_apisbrasilpro_async(rg_clean)
        res_serasa, res_dados = await asyncio.gather(t_serasa, t_dados, return_exceptions=True)

        found_cpf = None
        dados_direct = None

        if isinstance(res_serasa, dict) and res_serasa:
            dados_direct = res_serasa
            found_cpf = re.sub(r"\D", "", str(res_serasa.get("CPF") or res_serasa.get("cpf") or ""))

        if not found_cpf and isinstance(res_dados, dict):
            d_list = res_dados.get("dados") or res_dados.get("resultados") or []
            if isinstance(d_list, list) and d_list:
                found_cpf = re.sub(r"\D", "", str(d_list[0].get("cpf") or d_list[0].get("CPF") or ""))
            elif isinstance(d_list, dict):
                found_cpf = re.sub(r"\D", "", str(d_list.get("cpf") or d_list.get("CPF") or ""))

        if found_cpf and len(found_cpf) == 11:
            merged_data = await custom_merge_cpf_local(found_cpf)
            text_result = mod.format_result(merged_data, title="CONSULTA RG", user=user)
            await custom_send_result_with_txt(update, text_result, "CONSULTA RG", user_id, raw_data=merged_data, query_value=rg_clean, context=context)
        elif dados_direct:
            text_result = mod.format_result(dados_direct, title="CONSULTA RG", user=user)
            await custom_send_result_with_txt(update, text_result, "CONSULTA RG", user_id, raw_data=dados_direct, query_value=rg_clean, context=context)
        else:
            msg = await mod.send_chat_msg(context, update, mod.wrap_quote("📌 <b>RG</b>: nenhum resultado encontrado."), parse_mode="HTML")
            mod.auto_delete(msg, delay=15)
        await mod.delete_msg(msg_wait)
    except Exception as e:
        mod.logger.error(f"Erro no custom_cmd_rg: {e}")
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote(f"❌ Erro ao consultar RG: {mod.sanitize_error_msg(str(e))}"), parse_mode="HTML")
        mod.auto_delete(msg, delay=15)
        await mod.delete_msg(msg_wait)

# 11. Consulta de Veículos unificada com SPC2 Placa e Renavam
orig_consultar_veiculo_unificado = mod.consultar_veiculo_unificado

async def custom_consultar_veiculo_unificado(termo: str, tipo: str = "PLACA"):
    termo_str = str(termo or "").strip().upper()
    try:
        dados_veic = await orig_consultar_veiculo_unificado(termo_str, tipo)
    except Exception:
        dados_veic = {}

    if not isinstance(dados_veic, dict):
        dados_veic = {}

    if tipo == "PLACA":
        try:
            spc2_res = await asyncio.wait_for(apisbrasilpro_api.consultar_spc2_placa_apisbrasilpro_async(termo_str), timeout=8.0)
            if isinstance(spc2_res, dict) and spc2_res.get("resultados"):
                for it in spc2_res["resultados"]:
                    if isinstance(it, dict):
                        if not dados_veic.get("PLACA") and it.get("placa"):
                            dados_veic["PLACA"] = it["placa"]
                        if not dados_veic.get("RENAVAM") and it.get("renavan"):
                            dados_veic["RENAVAM"] = str(it["renavan"])
                        if not dados_veic.get("CHASSI") and it.get("chassi"):
                            dados_veic["CHASSI"] = it["chassi"]
                        if not dados_veic.get("ANO") and it.get("ano"):
                            dados_veic["ANO"] = str(it["ano"])
                        if not dados_veic.get("PROPRIETARIO_DOCUMENTO") and it.get("cpf_cnpj"):
                            dados_veic["PROPRIETARIO_DOCUMENTO"] = it["cpf_cnpj"]
        except Exception:
            pass
    elif tipo == "RENAVAM":
        try:
            spc2_res = await asyncio.wait_for(apisbrasilpro_api.consultar_spc2_renavam_apisbrasilpro_async(termo_str), timeout=8.0)
            if isinstance(spc2_res, dict) and spc2_res.get("resultados"):
                for it in spc2_res["resultados"]:
                    if isinstance(it, dict):
                        if not dados_veic.get("PLACA") and it.get("placa"):
                            dados_veic["PLACA"] = it["placa"]
                        if not dados_veic.get("RENAVAM") and it.get("renavan"):
                            dados_veic["RENAVAM"] = str(it["renavan"])
                        if not dados_veic.get("CHASSI") and it.get("chassi"):
                            dados_veic["CHASSI"] = it["chassi"]
        except Exception:
            pass

    return dados_veic

# 12. Consulta de CEP unificada com Credilink, SPC2, Telefone0, Telefone1
orig_consultar_cep_unificado = mod.consultar_cep_unificado

async def custom_consultar_cep_unificado(cep: str):
    cep_clean = re.sub(r"\D", "", str(cep or ""))
    try:
        dados_cep = await orig_consultar_cep_unificado(cep_clean)
    except Exception:
        dados_cep = {}

    if not isinstance(dados_cep, dict):
        dados_cep = {}

    try:
        t_cred = apisbrasilpro_api.consultar_credilink_cep_apisbrasilpro_async(cep_clean)
        t_spc2 = apisbrasilpro_api.consultar_spc2_cep_apisbrasilpro_async(cep_clean)
        r_cred, r_spc2 = await asyncio.gather(t_cred, t_spc2, return_exceptions=True)

        moradores = dados_cep.get("MORADORES") or []
        if isinstance(moradores, str):
            moradores = [moradores]

        seen_cpfs = set()
        for m in moradores:
            c = re.sub(r"\D", "", str(m))
            if len(c) == 11:
                seen_cpfs.add(c)

        for r in (r_cred, r_spc2):
            if isinstance(r, dict):
                items = r.get("results") or r.get("resultados") or []
                if isinstance(items, list):
                    for it in items:
                        if isinstance(it, dict):
                            cpf = re.sub(r"\D", "", str(it.get("CPF") or it.get("cpf") or it.get("cpf_cnpj") or ""))
                            nome = str(it.get("NOME") or it.get("nome") or "").strip().upper()
                            if cpf and cpf not in seen_cpfs:
                                seen_cpfs.add(cpf)
                                moradores.append(f"{nome} - CPF: {cpf}")

        if moradores:
            dados_cep["MORADORES"] = moradores
    except Exception:
        pass

    return dados_cep

# 13. Comandos Dedicados para as Novas APIs do ApisBrasilPro
async def custom_cmd_rais(update, context):
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return
    text = (update.message.text or "").strip() if update.message else ""
    parts = text.split(maxsplit=1)
    cpf_arg = parts[1].strip() if len(parts) > 1 else ""
    if not cpf_arg and getattr(context, "args", None):
        cpf_arg = context.args[0].strip()

    cpf = re.sub(r"\D", "", cpf_arg)
    if len(cpf) != 11:
        mod.user_states[user_id] = "btn_rais"
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote("Digite o CPF para consultar HISTÓRICO RAIS (CLT/Empregos):"), parse_mode="HTML")
        mod.auto_delete(msg, delay=60)
        return

    user = update.effective_user
    uname_tag = f"@{user.username}" if user and user.username else getattr(user, "full_name", "") or str(user_id)
    msg_wait = await mod.send_loading_message(context, update.effective_chat.id, uname_tag, update=update, text=f"🔍 {uname_tag} Consultando RAIS 2019... aguarde.")

    try:
        res = await asyncio.wait_for(apisbrasilpro_api.consultar_rais2019_cpf_async(cpf), timeout=15.0)
        data = res.get("data") if isinstance(res, dict) else None
        if not data:
            msg = await mod.send_chat_msg(context, update, mod.wrap_quote("📌 <b>RAIS 2019</b>: nenhum registro de vínculo empregatício encontrado."), parse_mode="HTML")
            mod.auto_delete(msg, delay=15)
            await mod.delete_msg(msg_wait)
            return

        lines = [f"💼 <b>HISTÓRICO DE EMPREGOS RAIS 2019</b>\nCPF: <code>{cpf}</code>\n"]
        for idx, item in enumerate(data, 1):
            empresa = item.get("NOM_RAZAO_") or item.get("NOM_FANTAS") or item.get("CNPJ") or "NÃO INFORMADO"
            linhas_item = [
                f"<b>Vínculo #{idx}</b>",
                f"• Trabalhador: {item.get('NOME_TRABALHADOR', 'N/D')}",
                f"• PIS: {item.get('PIS', 'N/D')}",
                f"• Empresa / CNPJ: {empresa}",
                f"• Cargo CBO: {item.get('CBO_2002', 'N/D')}",
                f"• Salário: R$ {item.get('SAL_MENSAL_DECLARADO') or item.get('SALARIO_MENSAL', 'N/D')}",
                f"• Admissão: {str(item.get('ADMISSAO', 'N/D'))[:10]}",
                f"• Horas Contratuais: {item.get('QTD_HORA_CONTRAT', 'N/D')}h",
                f"• Município: {item.get('MUNICIPIOIBGE', 'N/D')}/{item.get('UFIBGE', '')}"
            ]
            lines.append("\n".join(linhas_item))

        texto_final = "\n\n".join(lines)
        await custom_send_result_with_txt(update, texto_final, "CONSULTA RAIS 2019", user_id, raw_data=res, query_value=cpf, context=context)
        await mod.delete_msg(msg_wait)
    except Exception as e:
        mod.logger.error(f"Erro cmd_rais: {e}")
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote(f"❌ Erro ao consultar RAIS: {mod.sanitize_error_msg(str(e))}"), parse_mode="HTML")
        mod.auto_delete(msg, delay=15)
        await mod.delete_msg(msg_wait)

async def custom_cmd_paycom(update, context):
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return
    text = (update.message.text or "").strip() if update.message else ""
    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg and getattr(context, "args", None):
        arg = context.args[0].strip()

    num = re.sub(r"\D", "", arg)
    if not num:
        mod.user_states[user_id] = "btn_paycom"
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote("Digite o CPF ou TELEFONE para consultar HISTÓRICO PAYCOM:"), parse_mode="HTML")
        mod.auto_delete(msg, delay=60)
        return

    user = update.effective_user
    uname_tag = f"@{user.username}" if user and user.username else getattr(user, "full_name", "") or str(user_id)
    msg_wait = await mod.send_loading_message(context, update.effective_chat.id, uname_tag, update=update, text=f"🔍 {uname_tag} Consultando compras PayCom... aguarde.")

    try:
        if len(num) == 11 and num.startswith(("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")):
            res = await asyncio.wait_for(apisbrasilpro_api.consultar_paycom_identity_apisbrasilpro_async(num), timeout=15.0)
        else:
            t1 = apisbrasilpro_api.consultar_paycom_telephone_apisbrasilpro_async(num)
            t2 = apisbrasilpro_api.consultar_paycom2_telefone_apisbrasilpro_async(num)
            r1, r2 = await asyncio.gather(t1, t2, return_exceptions=True)
            res = r1 if isinstance(r1, dict) and r1.get("dados") else r2

        dados = res.get("dados") if isinstance(res, dict) else None
        if not dados:
            msg = await mod.send_chat_msg(context, update, mod.wrap_quote("📌 <b>PAYCOM</b>: nenhuma compra encontrada."), parse_mode="HTML")
            mod.auto_delete(msg, delay=15)
            await mod.delete_msg(msg_wait)
            return

        lines = [f"🛒 <b>HISTÓRICO DE COMPRAS PAYCOM</b>\nTermo: <code>{num}</code>\nTotal: {len(dados)}\n"]
        for idx, item in enumerate(dados, 1):
            linhas_item = [
                f"<b>Registro #{idx}</b>",
                f"• Nome: {item.get('name', 'N/D')}",
                f"• Documento: {item.get('identity', 'N/D')}",
                f"• Email: {item.get('email', 'N/D')}",
                f"• Telefone: {item.get('telephone', 'N/D')}",
                f"• Data do Registro: {item.get('created_at', 'N/D')}",
                f"• ID Comprador: {item.get('buyer_id', 'N/D')}"
            ]
            lines.append("\n".join(linhas_item))

        texto_final = "\n\n".join(lines)
        await custom_send_result_with_txt(update, texto_final, "CONSULTA PAYCOM", user_id, raw_data=res, query_value=num, context=context)
        await mod.delete_msg(msg_wait)
    except Exception as e:
        mod.logger.error(f"Erro cmd_paycom: {e}")
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote(f"❌ Erro ao consultar PayCom: {mod.sanitize_error_msg(str(e))}"), parse_mode="HTML")
        mod.auto_delete(msg, delay=15)
        await mod.delete_msg(msg_wait)

async def custom_cmd_operadora(update, context):
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return
    text = (update.message.text or "").strip() if update.message else ""
    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg and getattr(context, "args", None):
        arg = context.args[0].strip()

    if not arg:
        mod.user_states[user_id] = "btn_operadora"
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote("Digite o CPF, TELEFONE ou NOME para consultar OPERADORAS (Claro/Nextel/Cadsus):"), parse_mode="HTML")
        mod.auto_delete(msg, delay=60)
        return

    num = re.sub(r"\D", "", arg)
    user = update.effective_user
    uname_tag = f"@{user.username}" if user and user.username else getattr(user, "full_name", "") or str(user_id)
    msg_wait = await mod.send_loading_message(context, update.effective_chat.id, uname_tag, update=update, text=f"🔍 {uname_tag} Consultando bases de operadoras... aguarde.")

    try:
        if len(num) == 11:
            t_claro = apisbrasilpro_api.consultar_claro_cpf_async(num)
            t_cadsus = apisbrasilpro_api.consultar_cadsus_cpf_async(num)
            t_nextel = apisbrasilpro_api.consultar_nextel_cpf_async(num)
        elif len(num) in (10, 11):
            t_claro = apisbrasilpro_api.consultar_claro_telefone_async(num)
            t_cadsus = apisbrasilpro_api.consultar_cadsus_celular_async(num)
            t_nextel = apisbrasilpro_api.consultar_nextel_telefone_async(num)
        else:
            t_claro = apisbrasilpro_api.consultar_claro_nome_async(arg)
            t_cadsus = apisbrasilpro_api.consultar_cadsus_nome_async(arg)
            t_nextel = asyncio.sleep(0)

        r_claro, r_cadsus, r_nextel = await asyncio.gather(t_claro, t_cadsus, t_nextel, return_exceptions=True)

        combined = {"CLARO": r_claro, "CADSUS": r_cadsus, "NEXTEL": r_nextel}
        texto_parts = [f"📱 <b>CONSULTA OPERADORAS / CADSUS</b>\nTermo: <code>{arg}</code>\n"]

        has_data = False
        for op_name, resp in (("CLARO", r_claro), ("CADSUS", r_cadsus), ("NEXTEL", r_nextel)):
            if isinstance(resp, dict):
                d_list = resp.get("dados") or resp.get("data") or []
                if isinstance(d_list, list) and d_list:
                    has_data = True
                    texto_parts.append(f"<b>Base {op_name} ({len(d_list)} registros)</b>:")
                    for idx, it in enumerate(d_list[:5], 1):
                        campos = [f"• {k}: {v}" for k, v in it.items() if v and k not in ("id", "ID")]
                        texto_parts.append(f"<b>#{idx}</b>\n" + "\n".join(campos))

        if not has_data:
            msg = await mod.send_chat_msg(context, update, mod.wrap_quote("📌 <b>OPERADORAS</b>: nenhum resultado encontrado."), parse_mode="HTML")
            mod.auto_delete(msg, delay=15)
            await mod.delete_msg(msg_wait)
            return

        texto_final = "\n\n".join(texto_parts)
        await custom_send_result_with_txt(update, texto_final, "CONSULTA OPERADORAS", user_id, raw_data=combined, query_value=arg, context=context)
        await mod.delete_msg(msg_wait)
    except Exception as e:
        mod.logger.error(f"Erro cmd_operadora: {e}")
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote(f"❌ Erro ao consultar operadoras: {mod.sanitize_error_msg(str(e))}"), parse_mode="HTML")
        mod.auto_delete(msg, delay=15)
        await mod.delete_msg(msg_wait)

async def custom_cmd_br21m(update, context):
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return
    text = (update.message.text or "").strip() if update.message else ""
    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg and getattr(context, "args", None):
        arg = context.args[0].strip()

    num = re.sub(r"\D", "", arg)
    if not num:
        mod.user_states[user_id] = "btn_br21m"
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote("Digite o DOCUMENTO (CPF/CNPJ) ou TELEFONE para consultar BR 21M:"), parse_mode="HTML")
        mod.auto_delete(msg, delay=60)
        return

    user = update.effective_user
    uname_tag = f"@{user.username}" if user and user.username else getattr(user, "full_name", "") or str(user_id)
    msg_wait = await mod.send_loading_message(context, update.effective_chat.id, uname_tag, update=update, text=f"🔍 {uname_tag} Consultando BR 21M... aguarde.")

    try:
        if len(num) in (11, 14):
            res = await asyncio.wait_for(apisbrasilpro_api.consultar_br21m_doc_async(num), timeout=15.0)
        else:
            res = await asyncio.wait_for(apisbrasilpro_api.consultar_br21m_telefone_async(num), timeout=15.0)

        dados = res.get("data") if isinstance(res, dict) else None
        if not dados:
            msg = await mod.send_chat_msg(context, update, mod.wrap_quote("📌 <b>BR 21M</b>: nenhum resultado encontrado."), parse_mode="HTML")
            mod.auto_delete(msg, delay=15)
            await mod.delete_msg(msg_wait)
            return

        lines = [f"📊 <b>CONSULTA BASE BR 21M</b>\nTermo: <code>{num}</code>\nTotal: {len(dados)}\n"]
        for idx, it in enumerate(dados[:10], 1):
            linhas_item = [
                f"<b>Registro #{idx}</b>",
                f"• Nome: {it.get('NOME', 'N/D')}",
                f"• Documento: {it.get('DOC', 'N/D')}",
                f"• Telefone: ({it.get('DDD', '')}) {it.get('TELEFONE', '')}",
                f"• Endereço: {it.get('LOGRADOURO', '')}, {it.get('NUMERO', '')} - {it.get('CIDADE', '')}/{it.get('UF', '')}"
            ]
            lines.append("\n".join(linhas_item))

        texto_final = "\n\n".join(lines)
        await custom_send_result_with_txt(update, texto_final, "CONSULTA BR 21M", user_id, raw_data=res, query_value=num, context=context)
        await mod.delete_msg(msg_wait)
    except Exception as e:
        mod.logger.error(f"Erro cmd_br21m: {e}")
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote(f"❌ Erro ao consultar BR 21M: {mod.sanitize_error_msg(str(e))}"), parse_mode="HTML")
        mod.auto_delete(msg, delay=15)
        await mod.delete_msg(msg_wait)

async def custom_cmd_parente(update, context):
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return
    text = (update.message.text or "").strip() if update.message else ""
    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg and getattr(context, "args", None):
        arg = context.args[0].strip()

    cpf = re.sub(r"\D", "", arg)
    if not cpf:
        mod.user_states[user_id] = "btn_parente"
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote("Digite o CPF para consultar PARENTES (Serasa):"), parse_mode="HTML")
        mod.auto_delete(msg, delay=60)
        return

    user = update.effective_user
    uname_tag = f"@{user.username}" if user and user.username else getattr(user, "full_name", "") or str(user_id)
    msg_wait = await mod.send_loading_message(context, update.effective_chat.id, uname_tag, update=update, text=f"🔍 {uname_tag} Consultando parentes... aguarde.")

    try:
        res = await asyncio.wait_for(apisbrasilpro_api.consultar_serasa_cpf_parente_apisbrasilpro_async(cpf), timeout=15.0)
        if not res or not isinstance(res, dict):
            msg = await mod.send_chat_msg(context, update, mod.wrap_quote("📌 <b>PARENTES</b>: nenhum parente encontrado para este CPF."), parse_mode="HTML")
            mod.auto_delete(msg, delay=15)
            await mod.delete_msg(msg_wait)
            return

        text_result = mod.format_result(res, title="CONSULTA PARENTES (SERASA)", user=user)
        await custom_send_result_with_txt(update, text_result, "CONSULTA PARENTES", user_id, raw_data=res, query_value=cpf, context=context)
        await mod.delete_msg(msg_wait)
    except Exception as e:
        mod.logger.error(f"Erro cmd_parente: {e}")
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote(f"❌ Erro ao consultar parentes: {mod.sanitize_error_msg(str(e))}"), parse_mode="HTML")
        mod.auto_delete(msg, delay=15)
        await mod.delete_msg(msg_wait)

async def custom_cmd_situacao(update, context):
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return
    text = (update.message.text or "").strip() if update.message else ""
    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg and getattr(context, "args", None):
        arg = context.args[0].strip()

    cpf = re.sub(r"\D", "", arg)
    if len(cpf) != 11:
        mod.user_states[user_id] = "btn_situacao"
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote("Digite o CPF para consultar SITUAÇÃO CADASTRAL:"), parse_mode="HTML")
        mod.auto_delete(msg, delay=60)
        return

    user = update.effective_user
    uname_tag = f"@{user.username}" if user and user.username else getattr(user, "full_name", "") or str(user_id)
    msg_wait = await mod.send_loading_message(context, update.effective_chat.id, uname_tag, update=update, text=f"🔍 {uname_tag} Consultando situação cadastral... aguarde.")

    try:
        res = await asyncio.wait_for(apisbrasilpro_api.consultar_situacao_cpf_apisbrasilpro_async(cpf), timeout=15.0)
        data = res.get("data") if isinstance(res, dict) else res
        if not data or not isinstance(data, dict):
            msg = await mod.send_chat_msg(context, update, mod.wrap_quote("📌 <b>SITUAÇÃO CADASTRAL</b>: CPF não encontrado na Receita Federal."), parse_mode="HTML")
            mod.auto_delete(msg, delay=15)
            await mod.delete_msg(msg_wait)
            return

        text_result = mod.format_result(data, title="SITUAÇÃO CADASTRAL RECEITA FEDERAL", user=user)
        await custom_send_result_with_txt(update, text_result, "SITUAÇÃO CADASTRAL", user_id, raw_data=data, query_value=cpf, context=context)
        await mod.delete_msg(msg_wait)
    except Exception as e:
        mod.logger.error(f"Erro cmd_situacao: {e}")
        msg = await mod.send_chat_msg(context, update, mod.wrap_quote(f"❌ Erro ao consultar situação: {mod.sanitize_error_msg(str(e))}"), parse_mode="HTML")
        mod.auto_delete(msg, delay=15)
        await mod.delete_msg(msg_wait)

# 14. Handler de Mensagens Unificado com todos os estados interativos
orig_handle_message = mod.handle_message

async def custom_handle_message(update, context):
    user_id = update.effective_user.id if update.effective_user else None
    if user_id and user_id in mod.user_states:
        state = mod.user_states.pop(user_id, None)
        if state == "btn_foto":
            return await custom_cmd_foto(update, context)
        elif state == "btn_rais":
            return await custom_cmd_rais(update, context)
        elif state == "btn_paycom":
            return await custom_cmd_paycom(update, context)
        elif state == "btn_operadora":
            return await custom_cmd_operadora(update, context)
        elif state == "btn_br21m":
            return await custom_cmd_br21m(update, context)
        elif state == "btn_parente":
            return await custom_cmd_parente(update, context)
        elif state == "btn_situacao":
            return await custom_cmd_situacao(update, context)
        elif state == "btn_rg":
            return await custom_cmd_rg(update, context)

    return await orig_handle_message(update, context)

# 15. Atualiza lista de comandos disponíveis exibida no bot
NOVA_LISTA_COMANDOS = (
    f"{mod.COMMANDS_HELP_LIST}\n"
    f"/𝗿𝗮𝗶𝘀 𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵𝟬𝟭 (Histórico de Emprego/CLT)\n\n"
    f"/𝗽𝗮𝘆𝗰𝗼𝗺 𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵𝟬𝟭 (Histórico de Compras)\n\n"
    f"/𝗼𝗽𝗲𝗿𝗮𝗱𝗼𝗿𝗮 𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵𝟬𝟭 (Claro, Cadsus e Nextel)\n\n"
    f"/𝗯𝗿𝟮𝟭𝗺 𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵𝟬𝟭 (Base BR 21M)\n\n"
    f"/𝗽𝗮𝗿𝗲𝗻𝘁𝗲 𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵𝟬𝟭 (Parentes Serasa)\n\n"
    f"/𝘀𝗶𝘁𝘂𝗮𝗰𝗮𝗼 𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵𝟬𝟭 (Situação Receita Federal)"
)
mod.COMMANDS_HELP_LIST = NOVA_LISTA_COMANDOS

# 16. Overrides aplicados no mod
mod.get_delete_markup = custom_get_delete_markup
mod.send_result_with_txt = custom_send_result_with_txt
mod.button_handler = custom_button_handler
mod.post_init = custom_post_init
mod.buscar_todas_fotos_unificada = custom_buscar_todas_fotos_unificada
mod.buscar_foto_unificada = custom_buscar_foto_unificada
mod.merge_cpf_local = custom_merge_cpf_local
mod.buscar_por_nome = custom_buscar_por_nome
mod.buscar_por_telefone = custom_buscar_por_telefone
mod.cmd_foto = custom_cmd_foto
mod.cmd_rg = custom_cmd_rg
mod.consultar_veiculo_unificado = custom_consultar_veiculo_unificado
mod.consultar_cep_unificado = custom_consultar_cep_unificado
mod.handle_message = custom_handle_message
mod.check_access_and_chat = custom_check_access_and_chat

# 17. Registro de novos comandos no Telegram Application
orig_main = mod.main

def custom_main():
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
    TOKEN = mod.TOKEN

    application = Application.builder().token(TOKEN).post_init(custom_post_init).build()

    # Comandos originais
    application.add_handler(CommandHandler("start", mod.start))
    application.add_handler(CommandHandler("cpf", mod.cmd_cpf))
    application.add_handler(CommandHandler("rg", custom_cmd_rg))
    application.add_handler(CommandHandler("nome", mod.cmd_nome))
    application.add_handler(CommandHandler("telefone", mod.cmd_telefone))
    application.add_handler(CommandHandler("placa", mod.cmd_placa))
    application.add_handler(CommandHandler("chassi", mod.cmd_chassi))
    application.add_handler(CommandHandler("motor", mod.cmd_motor))
    application.add_handler(CommandHandler("renavam", mod.cmd_renavam))
    application.add_handler(CommandHandler("pai", mod.cmd_pai))
    application.add_handler(CommandHandler("mae", mod.cmd_mae))
    application.add_handler(CommandHandler("email", mod.cmd_email))
    application.add_handler(CommandHandler("cep", mod.cmd_cep))
    application.add_handler(CommandHandler("cnpj", mod.cmd_cnpj))
    application.add_handler(CommandHandler("foto", custom_cmd_foto))
    application.add_handler(CommandHandler(["processo", "processos", "proc"], mod.cmd_processo))
    application.add_handler(CommandHandler(["topic", "topico"], mod.cmd_topic))
    application.add_handler(CommandHandler(["ia", "ai"], mod.cmd_ia))
    application.add_handler(CommandHandler(["suporte", "support"], mod.cmd_suporte))

    # Novos comandos do ApisBrasilPro
    application.add_handler(CommandHandler(["rais", "trabalho", "emprego", "clt"], custom_cmd_rais))
    application.add_handler(CommandHandler(["paycom", "compras"], custom_cmd_paycom))
    application.add_handler(CommandHandler(["operadora", "claro", "nextel", "cadsus"], custom_cmd_operadora))
    application.add_handler(CommandHandler(["br21m", "br21"], custom_cmd_br21m))
    application.add_handler(CommandHandler(["parente", "parentes"], custom_cmd_parente))
    application.add_handler(CommandHandler(["situacao", "receita"], custom_cmd_situacao))

    # Handlers de callbacks e mensagens
    application.add_handler(CallbackQueryHandler(custom_button_handler))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, mod.new_member))
    application.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.ALL, mod.detect_group))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, custom_handle_message))

    print("[Bot] Iniciando com todas as 25 APIs do ApisBrasilPro integradas...")
    application.run_polling(allowed_updates=mod.Update.ALL_TYPES)

mod.main = custom_main

if __name__ == '__main__':
    custom_main()
