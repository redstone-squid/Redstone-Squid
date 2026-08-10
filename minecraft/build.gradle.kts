import org.gradle.api.plugins.JavaPluginExtension
import org.gradle.api.tasks.bundling.AbstractArchiveTask
import org.gradle.api.tasks.testing.Test
import org.gradle.jvm.toolchain.JavaLanguageVersion
import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import org.jetbrains.kotlin.gradle.dsl.KotlinJvmProjectExtension

plugins {
    base
    alias(libs.plugins.kotlin.jvm) apply false
    alias(libs.plugins.kotlin.serialization) apply false
    alias(libs.plugins.shadow) apply false
    alias(libs.plugins.fabric.loom) apply false
}

allprojects {
    group = "com.redstonesquid.minecraft"
    version = "0.1.0-SNAPSHOT"
}

subprojects {
    dependencyLocking {
        lockAllConfigurations()
    }

    pluginManager.withPlugin("java") {
        extensions.configure<JavaPluginExtension> {
            toolchain.languageVersion = JavaLanguageVersion.of(25)
            withSourcesJar()
        }
    }

    pluginManager.withPlugin("org.jetbrains.kotlin.jvm") {
        extensions.configure<KotlinJvmProjectExtension> {
            jvmToolchain(25)
            compilerOptions {
                allWarningsAsErrors.set(true)
                jvmTarget.set(JvmTarget.JVM_25)
                progressiveMode.set(true)
                freeCompilerArgs.addAll("-Xconsistent-data-class-copy-visibility", "-Xjsr305=strict")
            }
        }
    }

    tasks.withType<Test>().configureEach {
        useJUnitPlatform()
        testLogging {
            events("failed", "skipped")
        }
    }

    tasks.withType<AbstractArchiveTask>().configureEach {
        isPreserveFileTimestamps = false
        isReproducibleFileOrder = true
    }

    tasks.withType<Jar>().configureEach {
        from(rootProject.file("LICENSE")) {
            into("META-INF")
            rename { "LICENSE_RedstoneSquid" }
        }
    }
}

val buildModules = subprojects.filterNot { it.path == ":platform" }

tasks.named("assemble") {
    dependsOn(buildModules.map { "${it.path}:assemble" })
}

tasks.named("check") {
    dependsOn(buildModules.map { "${it.path}:check" })
}
