// QR rendering for pairing: black modules on white, 6 px per module, 4-module quiet zone.
import qrcode from "qrcode-generator";

function encode(text: string) {
  const qr = qrcode(0, "M");
  qr.addData(text);
  qr.make();
  return qr;
}

/** Draws text as a QR code. Returns false when the text cannot be encoded. */
export function drawQr(canvas: HTMLCanvasElement, text: string): boolean {
  let qr: ReturnType<typeof encode>;
  try {
    qr = encode(text);
  } catch {
    return false;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return false;
  const n = qr.getModuleCount();
  const cell = 6;
  const quiet = 4;
  const size = (n + quiet * 2) * cell;
  canvas.width = size;
  canvas.height = size;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, size, size);
  ctx.fillStyle = "#000000";
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) {
      if (qr.isDark(r, c)) ctx.fillRect((c + quiet) * cell, (r + quiet) * cell, cell, cell);
    }
  }
  return true;
}
