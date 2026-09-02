import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import https from 'https';
import http from 'http';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const TOKEN_FILE = path.join(__dirname, '..', 'data', 'radar_token.txt');
const FALLBACK_TOKEN = process.env.SERPRO_TOKEN || '';

const USUARIO = '03140433735';
const SENHA = 'Marcos1!';

const AUTH_URL = 'https://radar.serpro.gov.br/core-rest/gip-rest/auth/loginTalonario';
const BASE_URL = 'https://radar.serpro.gov.br/consultas-departamento-transito/api';

// Seed file with fallback token if file is empty and fallback exists
if (!fs.existsSync(TOKEN_FILE) || !fs.readFileSync(TOKEN_FILE, 'utf8').trim()) {
  if (FALLBACK_TOKEN) {
    try {
      fs.mkdirSync(path.dirname(TOKEN_FILE), { recursive: true });
      fs.writeFileSync(TOKEN_FILE, FALLBACK_TOKEN, 'utf8');
    } catch (_) {}
  }
}

function request(url, options = {}, body = null) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const lib = urlObj.protocol === 'https:' ? https : http;
    const req = lib.request(urlObj, {
      method: options.method || 'GET',
      headers: options.headers || {},
      rejectUnauthorized: false,
      ...options
    }, (res) => {
      const chunks = [];
      res.on('data', d => chunks.push(d));
      res.on('end', () => {
        const buf = Buffer.concat(chunks);
        let data;
        try { data = JSON.parse(buf.toString()); } catch { data = buf.toString(); }
        resolve({ status: res.statusCode, data, raw: buf.toString() });
      });
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

async function obterNovoToken() {
  const payload = JSON.stringify({
    imei: '550249443172777',
    latitude: 31.24916,
    longitude: 121.48789833333333,
    username: USUARIO,
    password: SENHA
  });

  try {
    const res = await request(AUTH_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'Dalvik/2.1.0 (Linux; Android 9)',
        'Content-Length': Buffer.byteLength(payload).toString()
      }
    }, payload);

    if (res.status !== 200) {
      console.error(`[SERPRO] Auth HTTP ${res.status}`);
      return null;
    }
    const json = typeof res.data === 'string' ? JSON.parse(res.data) : res.data;
    if (!json.token) {
      console.error(`[SERPRO] Auth resposta sem token`);
      return null;
    }
    const token = json.token.replace('Token ', '');
    try {
      fs.mkdirSync(path.dirname(TOKEN_FILE), { recursive: true });
      fs.writeFileSync(TOKEN_FILE, token, 'utf8');
    } catch (_) {}
    return token;
  } catch (e) {
    console.error(`[SERPRO] Auth error:`, e.message);
    return null;
  }
}

function lerToken() {
  try {
    const t = fs.readFileSync(TOKEN_FILE, 'utf8').trim();
    if (t) return t;
  } catch (_) {}
  return FALLBACK_TOKEN || null;
}

async function consultar(url) {
  let token = lerToken();
  if (!token) token = await obterNovoToken();
  if (!token) return { error: 'Falha ao obter token SERPRO' };

  try {
    let res = await request(url, {
      headers: {
        'Authorization': `Token ${token}`,
        'User-Agent': 'Dalvik/2.1.0 (Linux; Android 9)',
        'Connection': 'Keep-Alive'
      }
    });

    if (res.status === 403) {
      token = await obterNovoToken();
      if (!token) return { error: 'Token expirado' };
      res = await request(url, {
        headers: {
          'Authorization': `Token ${token}`,
          'User-Agent': 'Dalvik/2.1.0 (Linux; Android 9)',
          'Connection': 'Keep-Alive'
        }
      });
    }

    if (res.status !== 200) return { error: `HTTP ${res.status}` };
    return typeof res.data === 'string' ? JSON.parse(res.data) : res.data;
  } catch (e) {
    return { error: e.message };
  }
}

async function consultarPlaca(placa) {
  const p = placa.toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (p.length !== 7) return { error: 'Placa inválida. Use formato ABC1D23' };
  return consultar(`${BASE_URL}/veiculo/placa/${p}`);
}

async function consultarCpf(cpf) {
  const c = cpf.replace(/\D/g, '');
  if (c.length !== 11) return { error: 'CPF inválido. Use 11 dígitos' };
  return consultar(`${BASE_URL}/condutores/cpf/${c}/`);
}

export { consultarPlaca, consultarCpf };
