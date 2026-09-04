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
    if not todas_fotos and query_value:
        cpf_clean = re.sub(r"\D", "", str(query_value))
        if len(cpf_clean) == 11:
            try:
                fl, _ = await custom_buscar_todas_fotos_unificada(cpf_clean)
                if fl:
                    todas_fotos = fl
            except Exception:
                pass

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
                foto_banner = mod.add_spoiler_click_banner(foto_bytes)
                msg = await bot_instance.send_photo(
                    chat_id=chat_id,
                    photo=BytesIO(foto_banner),
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

# 6. Override para busca unificada de fotos com suporte a tconect Foto Nacional (DataVip)
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
                mod.consultar_todas_fotos_apisbrasilpro_async(cpf_clean),
                timeout=12.0
            )
        except Exception:
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

orig_merge_cpf_local = mod.merge_cpf_local

async def custom_merge_cpf_local(cpf):
    cpf_clean = re.sub(r"\D", "", str(cpf or ""))
    m_task = asyncio.create_task(orig_merge_cpf_local(cpf_clean))
    foto_task = asyncio.create_task(custom_buscar_todas_fotos_unificada(cpf_clean))

    try:
        merged, (fotos_list, _) = await asyncio.gather(m_task, foto_task)
    except Exception as e:
        mod.logger.error(f"Erro em custom_merge_cpf_local: {e}")
        merged = {}
        fotos_list = []

    if not isinstance(merged, dict):
        merged = {}

    if fotos_list:
        merged["_FOTO_BYTES"] = fotos_list[0][0]
        merged["_TODAS_FOTOS_BYTES"] = [f[0] for f in fotos_list]
        merged["_TODAS_FOTOS_LABELS"] = [f[1] for f in fotos_list]
        try:
            merged["_FOTO_BASE64"] = base64.b64encode(fotos_list[0][0]).decode("ascii")
            merged["_TODAS_FOTOS_BASE64"] = [base64.b64encode(f[0]).decode("ascii") for f in fotos_list]
        except Exception:
            pass

    return merged

async def custom_check_access_and_chat(update, context, user_id=None):
    # Permite consultas em qualquer chat (privado ou grupo)
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

    msg_wait = await mod.send_loading_message(context, update=update, text="🔍 Buscando foto... aguarde.")

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

orig_handle_message = mod.handle_message

async def custom_handle_message(update, context):
    user_id = update.effective_user.id if update.effective_user else None
    if user_id and mod.user_states.get(user_id) == "btn_foto":
        mod.user_states.pop(user_id, None)
        return await custom_cmd_foto(update, context)
    return await orig_handle_message(update, context)

# 7. Aplica overrides no bot
mod.get_delete_markup = custom_get_delete_markup
mod.send_result_with_txt = custom_send_result_with_txt
mod.button_handler = custom_button_handler
mod.post_init = custom_post_init
mod.buscar_todas_fotos_unificada = custom_buscar_todas_fotos_unificada
mod.buscar_foto_unificada = custom_buscar_foto_unificada
mod.merge_cpf_local = custom_merge_cpf_local
mod.cmd_foto = custom_cmd_foto
mod.handle_message = custom_handle_message
mod.check_access_and_chat = custom_check_access_and_chat

if __name__ == '__main__':
    mod.main()
