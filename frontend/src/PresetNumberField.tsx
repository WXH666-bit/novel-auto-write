import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";

export interface PresetNumberOption {
  value: number;
  label: string;
}

export const CONTEXT_LENGTH_PRESETS = [
  { value: 32_768, label: "32K" },
  { value: 65_536, label: "64K" },
  { value: 131_072, label: "128K" },
  { value: 262_144, label: "256K" },
  { value: 1_048_576, label: "1M" },
] as const satisfies readonly PresetNumberOption[];

export const MAX_OUTPUT_TOKENS_PRESETS = [
  { value: 4_096, label: "4K" },
  { value: 8_192, label: "8K" },
  { value: 16_384, label: "16K" },
  { value: 32_768, label: "32K" },
  { value: 65_536, label: "64K" },
] as const satisfies readonly PresetNumberOption[];

export interface PresetNumberFieldProps {
  label: string;
  value?: number;
  options: readonly PresetNumberOption[];
  min: number;
  onChange: (value: number) => void;
  helpText?: string;
}

/**
 * Returns the numeric value that should be displayed when a provider has no
 * saved value yet. Existing values are deliberately not rounded or clamped:
 * provider limits are allowed to be greater than the common presets.
 */
export function normalizePresetNumber(
  value: number | undefined,
  options: readonly PresetNumberOption[],
  fallback: number,
): number {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : options[0]?.value ?? fallback;
}

export function PresetNumberField({
  label,
  value,
  options,
  min,
  onChange,
  helpText = "常用档位；选择自定义可保留并填写其他数值。",
}: PresetNumberFieldProps) {
  const numericValue = normalizePresetNumber(value, options, min);
  const isPresetValue = useMemo(
    () => options.some((option) => option.value === numericValue),
    [numericValue, options],
  );
  const [customMode, setCustomMode] = useState(() => !isPresetValue);
  const previousValueRef = useRef(numericValue);
  const customOverrideRef = useRef(false);

  // A value loaded from the server that is not one of the presets must never
  // be silently converted to a preset when the parent refreshes the profile.
  useEffect(() => {
    if (numericValue === previousValueRef.current) return;
    if (!isPresetValue) {
      setCustomMode(true);
    } else if (!customOverrideRef.current) {
      // A changed preset usually means the selected provider was replaced or
      // refreshed. Do not leak the previous provider's custom mode into it.
      setCustomMode(false);
    }
    customOverrideRef.current = false;
    previousValueRef.current = numericValue;
  }, [isPresetValue, numericValue]);

  const selectedValue = customMode ? "custom" : String(numericValue);
  const handleSelect = (event: ChangeEvent<HTMLSelectElement>) => {
    if (event.target.value === "custom") {
      // Choosing custom is mode-only. Keep the current numeric value visible
      // so a user can adjust it without first reconstructing the old value.
      customOverrideRef.current = true;
      setCustomMode(true);
      return;
    }

    const nextValue = Number(event.target.value);
    const matchingOption = options.find((option) => option.value === nextValue);
    if (!matchingOption) return;
    customOverrideRef.current = false;
    setCustomMode(false);
    onChange(matchingOption.value);
  };

  const handleCustomChange = (event: ChangeEvent<HTMLInputElement>) => {
    const nextValue = event.currentTarget.valueAsNumber;
    if (Number.isFinite(nextValue) && nextValue >= min) onChange(nextValue);
  };

  return (
    <label className="field preset-number-field">
      <span>{label}</span>
      <div className="preset-number-control">
        <select
          aria-label={`${label}档位`}
          value={selectedValue}
          onChange={handleSelect}
        >
          {options.map((option) => (
            <option key={option.value} value={String(option.value)}>
              {option.label}
            </option>
          ))}
          <option value="custom">自定义</option>
        </select>
        {customMode && (
          <input
            aria-label={`${label}自定义值`}
            type="number"
            min={min}
            value={numericValue}
            onChange={handleCustomChange}
          />
        )}
      </div>
      <small className="preset-number-help">{helpText}</small>
    </label>
  );
}
