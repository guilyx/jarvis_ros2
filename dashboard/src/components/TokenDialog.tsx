import { useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState } from "react";
import { getToken, setToken } from "@/api/client";
import { useMe } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label } from "@/components/ui/input";
import { useLiveStream } from "@/live/context";

/** Who you are to the gateway: a token from its config names you in the audit trail. */
export function TokenDialog() {
  const me = useMe();
  const qc = useQueryClient();
  const live = useLiveStream();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(getToken);

  const save = (token: string) => {
    setToken(token);
    setOpen(false);
    void qc.invalidateQueries();
    live.restart();
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-muted cursor-pointer"
        aria-label="Identity"
      >
        <KeyRound size={14} className="text-muted-fg" />
        <span>{me.data?.name ?? "…"}</span>
        <span className="text-xs text-muted-fg">({me.data?.role ?? (me.isError ? "no access" : "…")})</span>
      </button>
      <DialogContent
        title="Identity"
        description="Paste a token from the gateway's settings (gateway.yaml or $JARVIS_GATEWAY_TOKENS). Without one you are the anonymous user."
      >
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            save(value.trim());
          }}
        >
          <label className="block">
            <Label>Token</Label>
            <Input
              type="password"
              className="w-full font-mono"
              aria-label="Token"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              autoComplete="off"
            />
          </label>
          <div className="flex justify-between gap-2">
            <Button variant="ghost" onClick={() => save("")}>
              Forget token
            </Button>
            <Button type="submit" variant="default">
              Save
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
