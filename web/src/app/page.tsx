import { ClosingCta } from "@/components/landing/ClosingCta";
import { EvidenceSplit } from "@/components/landing/EvidenceSplit";
import { Hero } from "@/components/landing/Hero";
import { HowItWorks } from "@/components/landing/HowItWorks";
import { Integrations } from "@/components/landing/Integrations";
import { Proof } from "@/components/landing/Proof";

/** Bands alternate tier down the page: hero · proof (alt) · steps · evidence (alt) · cards · close. */
export default function Home() {
  return (
    <>
      <Hero />
      <Proof />
      <HowItWorks />
      <EvidenceSplit />
      <Integrations />
      <ClosingCta />
    </>
  );
}
