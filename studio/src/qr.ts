// Pairing QR with a four-module quiet zone and an optional portrait.
import qrcode from "qrcode-generator";

function encode(text: string, correction: "H" | "M") {
  const qr = qrcode(0, correction);
  qr.addData(text);
  qr.make();
  return qr;
}

/** Draws text as a QR code. Returns false when the text cannot be encoded. */
export function drawQr(canvas: HTMLCanvasElement, text: string, portrait?: HTMLImageElement): boolean {
  let qr: ReturnType<typeof encode>;
  try {
    qr = encode(text, portrait ? "H" : "M");
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
  if (portrait) {
    // Keep the portrait within the central fifth of the data grid. Snap its
    // white border to module boundaries and leave the three finder patterns clear.
    const margin = Math.ceil(n * 0.395);
    const inset = (quiet + margin) * cell;
    const square = (n - 2 * margin) * cell;
    const imageSize = square - 2 * cell;
    const scale = imageSize / Math.max(portrait.naturalWidth, portrait.naturalHeight);
    const width = portrait.naturalWidth * scale;
    const height = portrait.naturalHeight * scale;
    ctx.fillStyle = "#fff";
    ctx.fillRect(inset, inset, square, square);
    ctx.drawImage(portrait, (size - width) / 2, (size - height) / 2, width, height);
  }
  return true;
}
