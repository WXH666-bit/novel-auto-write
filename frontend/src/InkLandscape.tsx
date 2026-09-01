type InkLandscapeProps = {
  className?: string;
  tone?: "light" | "dark";
};

/**
 * A code-native ink wash landscape. It stays decorative and deliberately
 * low-contrast so the writing surface remains the visual priority.
 */
export default function InkLandscape({
  className = "",
  tone = "light",
}: InkLandscapeProps) {
  return (
    <div
      className={`ink-landscape ink-landscape-${tone} ${className}`.trim()}
      aria-hidden="true"
    >
      <svg viewBox="0 0 1200 430" preserveAspectRatio="xMidYMax slice">
        <g className="ink-range ink-range-far">
          <path d="M0 306c104-18 168-65 242-118 37-27 67-20 108 22 32 33 63 39 107 10 64-42 114-96 184-79 49 12 60 53 106 66 62 18 91-61 161-55 60 6 86 79 130 100 53 26 101-1 162-15v193H0Z" />
        </g>
        <g className="ink-range ink-range-mid">
          <path d="M0 354c118-20 184-93 271-90 75 3 89 67 157 67 70 0 103-142 181-139 68 2 94 117 159 122 67 5 94-90 163-83 53 5 76 72 123 86 56 17 91-18 146-19v132H0Z" />
        </g>
        <g className="ink-range ink-range-near">
          <path d="M0 382c93-2 145-44 226-48 87-4 134 54 218 42 79-11 113-78 193-70 65 6 100 73 166 75 84 3 130-80 209-58 54 15 91 51 188 50v57H0Z" />
        </g>
        <circle className="ink-sun" cx="922" cy="116" r="24" />
        <path
          className="ink-ripple"
          d="M589 353c123-14 251-12 387 5M672 374c102-8 194-5 276 4M742 395c63-4 121-2 174 3"
        />
        <path
          className="ink-birds"
          d="M782 116c9-9 18-9 27 0 8-8 17-8 25 0M836 91c6-6 13-6 19 0 6-6 12-6 18 0"
        />
      </svg>
      <span className="ink-bloom ink-bloom-one" />
      <span className="ink-bloom ink-bloom-two" />
      <span className="ink-fleck ink-fleck-one" />
      <span className="ink-fleck ink-fleck-two" />
      <span className="ink-fleck ink-fleck-three" />
    </div>
  );
}
