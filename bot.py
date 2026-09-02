import os
import sys
import html
import re
import asyncio
from io import BytesIO
import importlib.abc
import importlib.util

base_dir = os.path.dirname(os.path.abspath(__file__))

class PycacheFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if path is None or '' in path or base_dir in (path or []):
            pyc_path = os.path.join(base_dir, '__pycache__', f'{fullname}.cpython-314.pyc')
            if os.path.exists(pyc_path):
                return importlib.util.spec_from_file_location(fullname, pyc_path)
        return None

sys.meta_path.insert(0, PycacheFinder())

# Carrega o módulo base compilado
spec = importlib.util.spec_from_file_location('bot_base', os.path.join(base_dir, '__pycache__/bot.cpython-314.pyc'))
mod = importlib.util.module_from_spec(spec)
sys.modules['bot_base'] = mod
spec.loader.exec_module(mod)

# Salva handlers originais
orig_button_handler = mod.button_handler

# 1. get_delete_markup sem botão de site
def custom_get_delete_markup(user_id, text=None, report_url=None, token=None):
    buttons = []
    # Removido completamente o link/botão para o site web
    if token:
        buttons.append([mod.InlineKeyboardButton("📁 Baixar TXT Resultado", callback_data=f"dltxt_{user_id}_{token}")])
    buttons.append([mod.InlineKeyboardButton("📋 Copiar Dados", callback_data=f"copiar_{user_id}")])
    buttons.append([mod.InlineKeyboardButton("🗑️ Apagar", callback_data=f"apagar_{user_id}")])
    return mod.InlineKeyboardMarkup(buttons)

# 2. send_result_with_txt enviando direto no PV e com botão de abrir no PV no grupo
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

    todas_fotos = []
    if isinstance(raw_data, dict):
        for item in raw_data.values():
            if isinstance(item, dict) and item.get("_TODAS_FOTOS_BYTES"):
                lbls = item.get("_TODAS_FOTOS_LABELS", [])
                for idx_f, fb in enumerate(item["_TODAS_FOTOS_BYTES"]):
                    lbl = lbls[idx_f] if idx_f < len(lbls) else f"Foto {idx_f+1}"
                    todas_fotos.append((fb, lbl))
                break
            elif isinstance(item, dict) and item.get("_FOTO_BYTES"):
                todas_fotos = [(item["_FOTO_BYTES"], "Foto")]
                break

    foto_bytes = todas_fotos[0][0] if todas_fotos else None

    async def _send_to_pv():
        clean_text = str(text).strip().replace("<blockquote>", "").replace("</blockquote>", "")
        markup_pv = custom_get_delete_markup(user_id, clean_text, report_url=None, token=token)

        # Envia foto no PV
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

        buttons_group = [
            [mod.PrimaryButton("📩 Ver Resultado no PV", callback_data=f"openpv_{user_id}_{token}")],
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

# 3. button_handler com bloqueio exclusivo para terceiros e envio seguro no PV
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
            todas_fotos = []
            if isinstance(raw_data, dict):
                for item in raw_data.values():
                    if isinstance(item, dict) and item.get("_TODAS_FOTOS_BYTES"):
                        lbls = item.get("_TODAS_FOTOS_LABELS", [])
                        for idx_f, fb in enumerate(item["_TODAS_FOTOS_BYTES"]):
                            lbl = lbls[idx_f] if idx_f < len(lbls) else f"Foto {idx_f+1}"
                            todas_fotos.append((fb, lbl))
                        break
                    elif isinstance(item, dict) and item.get("_FOTO_BYTES"):
                        todas_fotos = [(item["_FOTO_BYTES"], "Foto")]
                        break

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

# Aplica as alterações no módulo
mod.get_delete_markup = custom_get_delete_markup
mod.send_result_with_txt = custom_send_result_with_txt
mod.button_handler = custom_button_handler

if __name__ == '__main__':
    mod.main()
