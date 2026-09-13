import { Hero } from "@/components/landing/Hero";
import { HowItWorks } from "@/components/landing/HowItWorks";
import { Integrations } from "@/components/landing/Integrations";
import { Proof } from "@/components/landing/Proof";

export default function Home() {
  return (
    <>
      <Hero />
      <HowItWorks />
      <Proof />
      <Integrations />
    </>
  );
}
