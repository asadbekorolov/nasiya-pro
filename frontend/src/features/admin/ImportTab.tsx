import { CheckCircle2, Download, FileSpreadsheet, Package, RotateCcw, Store as StoreIcon, Upload, XCircle } from "lucide-react";
import { useRef, useState, type DragEvent, type ReactNode } from "react";

import { absoluteApiUrl, ApiError } from "@/api/client";
import { adminApi } from "@/api/endpoints";
import type { ImportKind, ImportResult } from "@/api/types";
import { Button, InlineError, cx } from "@/components/ui";
import { toast } from "@/components/toast";
import { formatMoney, toCents } from "@/lib/money";
import { confirm, haptic, nativeDownload } from "@/telegram/twa";

import { Badge } from "./widgets";

const MAX_BYTES = 5 * 1024 * 1024;
const ERRORS_SHOWN = 50;

const KINDS: Record<ImportKind, { title: string; icon: ReactNode; noun: string; hint: string; file: string }> = {
  products: {
    title: "Tovarlar",
    icon: <Package />,
    noun: "tovar",
    hint: "Nomi, SKU, narx va boshlang'ich qoldiq. Mavjud tovarlar SKU bo'yicha yangilanadi.",
    file: "nasiya-pro-tovarlar-namuna.xlsx",
  },
  stores: {
    title: "Do'konlar va qarzlar",
    icon: <StoreIcon />,
    noun: "do'kon",
    hint: "Do'kon, mas'ul shaxs, telefon, kredit limiti va hozirgi qarzi. Qarz akt-sverkaga dastlabki qoldiq bo'lib yoziladi.",
    file: "nasiya-pro-dokonlar-namuna.xlsx",
  },
};

type Stage =
  | { step: "idle" }
  | { step: "checking"; file: File }
  | { step: "preview"; file: File; result: ImportResult }
  | { step: "importing"; file: File; result: ImportResult }
  | { step: "done"; result: ImportResult };

function downloadTemplate(kind: ImportKind) {
  const url = absoluteApiUrl(adminApi.templatePath(kind));
  haptic.tap();
  if (nativeDownload(url, KINDS[kind].file)) return;
  const link = document.createElement("a");
  link.href = url;
  link.download = KINDS[kind].file;
  document.body.append(link);
  link.click();
  link.remove();
}

/** Excel onboarding: download a template, drop the filled file, review, import. */
export function ImportTab({ onImported }: { onImported: () => void }) {
  return (
    <div className="space-y-3">
      <p className="px-1 text-sm text-tg-hint">
        Mavjud tovarlar va do'konlar qarzini Excel orqali bir martada kiriting: namunani yuklab oling, to'ldiring va
        shu yerga tashlang. Avval tekshiruv natijasi ko'rsatiladi — tasdiqlamaguningizcha hech narsa yozilmaydi.
      </p>
      <div className="grid gap-3 lg:grid-cols-2">
        <ImportCard kind="products" onImported={onImported} />
        <ImportCard kind="stores" onImported={onImported} />
      </div>
    </div>
  );
}

function ImportCard({ kind, onImported }: { kind: ImportKind; onImported: () => void }) {
  const meta = KINDS[kind];
  const input = useRef<HTMLInputElement>(null);
  const [stage, setStage] = useState<Stage>({ step: "idle" });
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string>();

  async function check(file: File) {
    setError(undefined);
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setError("Faqat .xlsx fayl. Excelda «Saqlash» → «Excel kitobi (.xlsx)» ni tanlang.");
      return;
    }
    if (file.size > MAX_BYTES) {
      setError("Fayl 5 MB dan katta.");
      return;
    }
    setStage({ step: "checking", file });
    try {
      const result = await adminApi.importFile(kind, file, true);
      setStage({ step: "preview", file, result });
    } catch (err) {
      setStage({ step: "idle" });
      setError(err instanceof ApiError ? err.message : "Faylni tekshirib bo'lmadi");
    }
  }

  async function commit(file: File, preview: ImportResult) {
    if (kind === "stores" && toCents(preview.opening_debt_total) > 0n) {
      const ok = await confirm(
        `${preview.created} ta do'kon yaratiladi va jami ${formatMoney(preview.opening_debt_total)} ` +
          "boshlang'ich qarz hisobga yoziladi. Davom etasizmi?",
      );
      if (!ok) return;
    }
    setStage({ step: "importing", file, result: preview });
    setError(undefined);
    try {
      const result = await adminApi.importFile(kind, file, false);
      haptic.success();
      toast.success(`${result.imported} ta ${meta.noun} kiritildi`);
      setStage({ step: "done", result });
      onImported();
    } catch (err) {
      haptic.error();
      setStage({ step: "preview", file, result: preview });
      setError(err instanceof ApiError ? err.message : "Import bajarilmadi");
    }
  }

  function reset() {
    setStage({ step: "idle" });
    setError(undefined);
    if (input.current) input.current.value = "";
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) void check(file);
  }

  const busy = stage.step === "checking" || stage.step === "importing";
  const result = "result" in stage ? stage.result : undefined;

  return (
    <section className="flex flex-col gap-3 rounded-2xl bg-tg-section p-4">
      <div className="flex items-start gap-3">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-tg-button/12 text-tg-link [&>svg]:size-5">
          {meta.icon}
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold">{meta.title}</h2>
          <p className="text-sm text-tg-hint">{meta.hint}</p>
        </div>
      </div>

      <Button variant="secondary" className="min-h-10 text-sm" icon={<Download className="size-4" />} onClick={() => downloadTemplate(kind)}>
        Namuna Excel faylini yuklab olish
      </Button>

      {(stage.step === "idle" || stage.step === "checking") && (
        <label
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cx(
            "flex cursor-pointer flex-col items-center gap-2 rounded-2xl border-2 border-dashed px-4 py-8 text-center transition",
            dragging ? "border-tg-button bg-tg-button/8" : "border-tg-separator",
            busy && "pointer-events-none opacity-60",
          )}
        >
          <Upload className={cx("size-8 text-tg-hint", stage.step === "checking" && "animate-bounce")} aria-hidden />
          <span className="text-[15px] font-medium">
            {stage.step === "checking" ? `${stage.file.name} tekshirilmoqda…` : "Faylni shu yerga tashlang yoki tanlang"}
          </span>
          <span className="text-xs text-tg-hint">.xlsx, 5 MB gacha, 5000 qatorgacha</span>
          <input
            ref={input}
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            className="sr-only"
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void check(file);
            }}
          />
        </label>
      )}

      <InlineError message={error} />

      {result && (
        <ResultView
          kind={kind}
          result={result}
          fileName={stage.step === "done" ? undefined : "file" in stage ? stage.file.name : undefined}
        />
      )}

      {(stage.step === "preview" || stage.step === "importing") && (
        <div className="flex gap-2">
          <Button
            className="flex-1"
            icon={<CheckCircle2 className="size-5" />}
            loading={stage.step === "importing"}
            disabled={stage.result.imported === 0}
            onClick={() => void commit(stage.file, stage.result)}
          >
            {stage.result.imported > 0 ? `${stage.result.imported} ta qatorni kiritish` : "Kiritadigan qator yo'q"}
          </Button>
          <Button variant="ghost" disabled={stage.step === "importing"} onClick={reset}>
            Bekor
          </Button>
        </div>
      )}

      {stage.step === "done" && (
        <Button variant="secondary" icon={<RotateCcw className="size-4" />} onClick={reset}>
          Yana fayl yuklash
        </Button>
      )}
    </section>
  );
}

function ResultView({ kind, result, fileName }: { kind: ImportKind; result: ImportResult; fileName?: string }) {
  const noun = KINDS[kind].noun;
  const ok = result.imported;
  const done = !result.dry_run;
  return (
    <div className="space-y-3 rounded-2xl bg-tg-secondary p-3">
      <div className="flex items-center gap-2 text-sm">
        <FileSpreadsheet className="size-4 shrink-0 text-tg-hint" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-tg-hint">{fileName ?? "Natija"}</span>
        <Badge tone={done ? "success" : "info"}>{done ? "Kiritildi" : "Tekshiruv"}</Badge>
      </div>

      <ul className="space-y-1.5 text-[15px]">
        <li className="flex items-center gap-2">
          <CheckCircle2 className="size-5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
          <span>
            <b>
              {ok} ta {noun}
            </b>{" "}
            {done ? "muvaffaqiyatli kiritildi" : "kiritishga tayyor"}
            {kind === "products" && ok > 0 && (
              <span className="text-tg-hint">
                {" "}
                ({result.created} ta yangi, {result.updated} ta {done ? "yangilandi" : "yangilanadi"})
              </span>
            )}
          </span>
        </li>
        {kind === "stores" && toCents(result.opening_debt_total) > 0n && (
          <li className="pl-7 text-sm text-tg-hint">
            Boshlang'ich qarz jami: <b className="text-tg-text">{formatMoney(result.opening_debt_total)}</b>
          </li>
        )}
        {result.failed_rows > 0 && (
          <li className="flex items-center gap-2">
            <XCircle className="size-5 shrink-0 text-tg-destructive" aria-hidden />
            <span>
              <b className="text-tg-destructive">{result.failed_rows} ta qator xato</b>
              <span className="text-tg-hint"> — {done ? "kiritilmadi" : "kiritilmaydi"}</span>
            </span>
          </li>
        )}
      </ul>

      {result.errors.length > 0 && (
        <div className="max-h-72 overflow-auto rounded-xl bg-tg-section">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 bg-tg-section text-xs text-tg-hint">
              <tr>
                <th className="px-3 py-2 font-medium">Qator</th>
                <th className="px-3 py-2 font-medium">Ustun</th>
                <th className="px-3 py-2 font-medium">Sabab</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-tg-separator">
              {result.errors.slice(0, ERRORS_SHOWN).map((e, i) => (
                <tr key={`${e.row}-${i}`} className="align-top">
                  <td className="px-3 py-2 font-semibold whitespace-nowrap">{e.row}</td>
                  <td className="px-3 py-2 text-tg-hint">{e.column ?? "—"}</td>
                  <td className="px-3 py-2">{e.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.errors.length > ERRORS_SHOWN && (
            <p className="px-3 py-2 text-xs text-tg-hint">va yana {result.errors.length - ERRORS_SHOWN} ta xato…</p>
          )}
        </div>
      )}
      {result.failed_rows > 0 && (
        <p className="text-xs text-tg-hint">
          Xatolarni Excelda to'g'rilab, faylni qayta yuklang:{" "}
          {kind === "products" ? "tovarlar SKU bo'yicha yangilanadi" : "mavjud do'konlar qayta yaratilmaydi"}.
        </p>
      )}
    </div>
  );
}
