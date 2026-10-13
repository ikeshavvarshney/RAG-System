import type { SVGProps } from "react";

function Icon({ children, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 20 20"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

type Props = SVGProps<SVGSVGElement>;

export const ArrowUp = (props: Props) => (
  <Icon {...props}>
    <path d="M10 16V4M5 9l5-5 5 5" />
  </Icon>
);

export const ArrowRight = (props: Props) => (
  <Icon {...props}>
    <path d="M4 10h12M11 5l5 5-5 5" />
  </Icon>
);

export const Stop = (props: Props) => (
  <Icon {...props}>
    <rect x="5.5" y="5.5" width="9" height="9" rx="1.5" />
  </Icon>
);

export const Chevron = (props: Props) => (
  <Icon {...props}>
    <path d="M7 4l6 6-6 6" />
  </Icon>
);

export const Upload = (props: Props) => (
  <Icon {...props}>
    <path d="M10 13V3M6 7l4-4 4 4M4 13v2.5A1.5 1.5 0 0 0 5.5 17h9a1.5 1.5 0 0 0 1.5-1.5V13" />
  </Icon>
);

export const Doc = (props: Props) => (
  <Icon {...props}>
    <path d="M11.5 3H6.5A1.5 1.5 0 0 0 5 4.5v11A1.5 1.5 0 0 0 6.5 17h7a1.5 1.5 0 0 0 1.5-1.5v-9L11.5 3z" />
    <path d="M11.5 3v3.5H15" />
  </Icon>
);

export const Globe = (props: Props) => (
  <Icon {...props}>
    <circle cx="10" cy="10" r="7" />
    <path d="M3 10h14M10 3c2.2 2 3.2 4.3 3.2 7S12.2 15 10 17c-2.2-2-3.2-4.3-3.2-7S7.800 5 10 3z" />
  </Icon>
);

export const Copy = (props: Props) => (
  <Icon {...props}>
    <rect x="7" y="7" width="9.5" height="9.5" rx="1.5" />
    <path d="M13 7V5a1.5 1.5 0 0 0-1.5-1.5H5A1.5 1.5 0 0 0 3.5 5v6.5A1.5 1.5 0 0 0 5 13h2" />
  </Icon>
);

export const Check = (props: Props) => (
  <Icon {...props}>
    <path d="M4 10.5l4 4 8-9" />
  </Icon>
);

export const Close = (props: Props) => (
  <Icon {...props}>
    <path d="M5 5l10 10M15 5L5 15" />
  </Icon>
);

export const Play = (props: Props) => (
  <Icon {...props}>
    <path d="M7 4.5v11l9-5.5-9-5.5z" />
  </Icon>
);
