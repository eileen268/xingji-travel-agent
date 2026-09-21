"use client";

import { useId, useMemo, useState } from "react";
import { Check, MagnifyingGlass, MapPin } from "@phosphor-icons/react";
import { searchChinaCities, type ChinaCityOption } from "@/data/china-locations";

const noExclusions: string[] = [];

export function LocationSearch({ label, value, placeholder, onSelect, exclude = noExclusions }: { label: string; value?: string; placeholder: string; onSelect: (value: string) => void; exclude?: string[] }) {
  const id = useId();
  const [query, setQuery] = useState(value ?? "");
  const [open, setOpen] = useState(false);
  const items = useMemo(() => query.trim() === value ? [] : searchChinaCities(query, exclude), [exclude, query, value]);
  const message = query.trim() && query.trim() !== value && !items.length ? "固定城市库中没有匹配结果" : "";

  const choose = (item: ChinaCityOption) => {
    setQuery(item.name); setOpen(false); onSelect(item.name);
  };

  return <div className="location-picker">
    <label htmlFor={id}>{label}</label>
    <div className={`location-search-control ${open ? "open" : ""}`}>
      <MagnifyingGlass size={17}/><input id={id} value={query} placeholder={placeholder} autoComplete="off" onFocus={() => setOpen(true)} onChange={(event) => { setQuery(event.target.value); setOpen(true); if (value) onSelect(""); }} />
      {value && query === value ? <Check size={17} weight="bold"/> : null}
    </div>
    {open && <div className="location-suggestions" role="listbox">
      {items.map((item) => <button type="button" role="option" aria-selected="false" key={`${item.province}-${item.name}`} onClick={() => choose(item)}><MapPin size={18} weight="duotone"/><span><b>{item.name}</b><small>{item.province} · 本地城市库</small></span></button>)}
      {message && <p>{message}</p>}
    </div>}
  </div>;
}
